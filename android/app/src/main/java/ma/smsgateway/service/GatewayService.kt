package ma.smsgateway.service

import android.annotation.SuppressLint
import android.app.Notification
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ServiceInfo
import android.os.BatteryManager
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.telephony.TelephonyManager
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withTimeoutOrNull
import ma.smsgateway.App
import ma.smsgateway.BuildConfig
import ma.smsgateway.R
import ma.smsgateway.data.OutStatus
import ma.smsgateway.net.ApiException
import ma.smsgateway.net.SocketClient
import ma.smsgateway.sms.ErrorCodes
import ma.smsgateway.sms.SimManager
import ma.smsgateway.sms.SmsSender
import ma.smsgateway.ui.MainActivity
import org.json.JSONObject
import java.util.Calendar

/**
 * Service de premier plan : maintient la liaison avec le serveur et exécute les envois.
 *
 *  - WebSocket (Socket.IO) pour recevoir les tâches en temps réel ;
 *  - repli en polling HTTP toutes les 15 s si le WebSocket est coupé ;
 *  - heartbeat (batterie, signal, SIM, file locale) toutes les 30 s ;
 *  - limiteur d'envoi (délai entre SMS, max / heure, max / jour, seuil de batterie) ;
 *  - boîtes d'envoi locales (statuts, SMS reçus) rejouées dès que la connexion revient.
 */
class GatewayService : Service(), SocketClient.Listener {

    private val app by lazy { App.from(this) }
    private val prefs by lazy { app.prefs }
    private val db by lazy { app.db }
    private val log by lazy { app.log }
    private val repo by lazy { GatewayRepository(this) }
    private val sims by lazy { SimManager(this) }
    private val sender by lazy { SmsSender(this, sims) }

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val flushMutex = Mutex()
    private var socket: SocketClient? = null
    private var wakeLock: PowerManager.WakeLock? = null
    private var loops: List<Job> = emptyList()
    private var lastNotificationText = ""

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        startInForeground(getString(R.string.status_connecting))
        acquireWakeLock()
        running = true
        GatewayState.set { it.copy(serviceRunning = true, link = GatewayState.Link.CONNECTING) }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                stopSelfCleanly()
                return START_NOT_STICKY
            }
            ACTION_RECONNECT -> restartLink()
        }
        if (!prefs.isPaired) {
            log.warn("Service arrêté : appareil non appairé")
            stopSelfCleanly()
            return START_NOT_STICKY
        }
        if (loops.isEmpty()) startLoops()
        return START_STICKY
    }

    override fun onDestroy() {
        running = false
        socket?.disconnect()
        socket = null
        scope.cancel()
        wakeLock?.takeIf { it.isHeld }?.release()
        GatewayState.set { it.copy(serviceRunning = false, link = GatewayState.Link.OFFLINE) }
        super.onDestroy()
    }

    // --- Boucles -------------------------------------------------------------------------

    private fun startLoops() {
        log.info("Passerelle démarrée (${prefs.serverUrl})")
        connectSocket()
        loops = listOf(
            scope.launch { heartbeatLoop() },
            scope.launch { pollLoop() },
            scope.launch { senderLoop() },
            scope.launch { flushLoop() },
            scope.launch { housekeepingLoop() },
        )
    }

    private fun connectSocket() {
        val token = prefs.token ?: return
        socket = SocketClient(prefs.serverUrl, token, app.api.http, this).also { it.connect() }
    }

    private fun restartLink() {
        socket?.disconnect()
        socket = null
        if (prefs.isPaired) connectSocket()
    }

    private suspend fun heartbeatLoop() {
        while (scope.isActive) {
            runCatching { sendHeartbeat() }.onFailure { handleNetworkError("heartbeat", it) }
            delay(prefs.heartbeatSec * 1000L)
        }
    }

    private suspend fun sendHeartbeat() {
        val payload = heartbeatPayload()
        val ack = socket?.emitWithAck("heartbeat", payload)
        if (ack == null) {
            val res = app.api.heartbeat(payload)
            res.optJSONObject("config")?.let { prefs.applyConfig(it) }
        }
        GatewayState.set { it.copy(lastHeartbeat = System.currentTimeMillis(), lastError = null) }
    }

    /** Repli HTTP : actif uniquement quand le WebSocket est déconnecté. */
    private suspend fun pollLoop() {
        delay(5_000)
        while (scope.isActive) {
            if (socket?.isConnected != true) {
                runCatching {
                    val tasks = app.api.tasks()
                    if (tasks.length() > 0) {
                        val ids = repo.enqueueTasks(tasks)
                        app.api.ack(ids)
                        wake()
                    }
                    GatewayState.set { it.copy(link = GatewayState.Link.POLLING, lastError = null) }
                    flush()
                }.onFailure {
                    handleNetworkError("polling", it)
                    GatewayState.set { s -> s.copy(link = GatewayState.Link.OFFLINE) }
                }
            }
            delay(POLL_INTERVAL_MS)
        }
    }

    private suspend fun senderLoop() {
        while (scope.isActive) {
            val waitMs = trySendNext()
            // Réveil anticipé quand une tâche arrive, sinon attente du délai calculé
            withTimeoutOrNull(waitMs.coerceAtLeast(200)) { wakeups.receive() }
        }
    }

    /** Envoie le prochain SMS si le limiteur le permet. Retourne le délai avant le prochain essai (ms). */
    private suspend fun trySendNext(): Long {
        val next = db.outgoing().nextPending() ?: run { setBlocked(null); return 5_000 }
        val now = System.currentTimeMillis()

        blockReason(now)?.let { (reason, wait) -> setBlocked(reason); return wait }
        val last = db.outgoing().lastSendingAt() ?: 0L
        val interval = prefs.sendIntervalSec * 1000L
        if (now - last < interval) return interval - (now - last)
        setBlocked(null)

        when (val result = sender.send(next, prefs.defaultSimSlot)) {
            is SmsSender.Result.Submitted -> {
                db.outgoing().markSending(next.id, result.parts)
                log.info("Envoi → ${next.phone} (SIM ${(result.simSlot ?: 0) + 1}, ${result.parts} segment(s))")
            }
            is SmsSender.Result.Failed -> {
                db.outgoing().markSending(next.id, 0)
                db.outgoing().setStatus(next.id, OutStatus.FAILED, result.errorCode)
                repo.report(next.id, "failed", result.errorCode, result.message, next.simSlot)
                log.error("Échec immédiat → ${next.phone} : ${result.errorCode}")
                wakeups.trySend(Unit)
            }
        }
        return interval
    }

    /** Raison de blocage et délai avant nouvelle vérification, ou null si l'envoi est autorisé. */
    private suspend fun blockReason(now: Long): Pair<String, Long>? {
        if (prefs.paused) return getString(R.string.blocked_paused) to 10_000
        if (!prefs.serverEnabled) return getString(R.string.blocked_disabled) to 30_000
        val (level, charging) = battery()
        if (prefs.minBatteryPercent > 0 && level in 0 until prefs.minBatteryPercent && !charging) {
            return getString(R.string.blocked_battery, level) to 60_000
        }
        if (db.outgoing().countSendingSince(now - HOUR) >= prefs.maxPerHour) {
            return getString(R.string.blocked_hour, prefs.maxPerHour) to 60_000
        }
        if (db.outgoing().countSendingSince(startOfDay()) >= prefs.maxPerDay) {
            return getString(R.string.blocked_day, prefs.maxPerDay) to 300_000
        }
        return null
    }

    private fun setBlocked(reason: String?) {
        if (GatewayState.state.value.blockedReason != reason) {
            if (reason != null) log.warn(reason)
            GatewayState.set { it.copy(blockedReason = reason) }
            updateNotification()
        }
    }

    private suspend fun flushLoop() {
        while (scope.isActive) {
            withTimeoutOrNull(FLUSH_INTERVAL_MS) { flushRequests.receive() }
            runCatching { flush() }.onFailure { handleNetworkError("synchronisation", it) }
        }
    }

    /** Vide les boîtes d'envoi locales (statuts puis SMS reçus) vers le serveur. */
    private suspend fun flush() = flushMutex.withLock {
        while (true) {
            val reports = db.statuses().batch(100)
            if (reports.isEmpty()) break
            val payload = repo.reportsToJson(reports)
            val ok = socket?.emitWithAck("sms.status", JSONObject().put("statuses", payload))?.optBoolean("ok", false)
            if (ok != true) app.api.statuses(payload)
            db.statuses().delete(reports.map { it.rowId })
        }
        while (true) {
            val items = db.incoming().unsynced(50)
            if (items.isEmpty()) break
            val payload = repo.incomingToJson(items)
            val ack = socket?.emitWithAck("sms.received", JSONObject().put("messages", payload))
            val received = if (ack?.optBoolean("ok") == true) {
                val arr = ack.optJSONArray("received")
                (0 until (arr?.length() ?: 0)).map { arr!!.optString(it) }
            } else {
                app.api.inbound(payload)
            }
            if (received.isEmpty()) break
            db.incoming().markSynced(received)
        }
    }

    private suspend fun housekeepingLoop() {
        while (scope.isActive) {
            delay(60_000)
            runCatching {
                val now = System.currentTimeMillis()
                // Aucun accusé d'envoi après 3 min : échec temporaire, le serveur relancera
                db.outgoing().stuckSending(now - SENDING_TIMEOUT_MS).forEach { sms ->
                    db.outgoing().setStatus(sms.id, OutStatus.FAILED, ErrorCodes.TIMEOUT)
                    repo.report(sms.id, "failed", ErrorCodes.TIMEOUT, "Pas d'accusé d'envoi", sms.simSlot)
                    log.warn("Délai dépassé → ${sms.phone}")
                }
                db.outgoing().purgeOld(now - RETENTION_MS)
                db.incoming().purgeOld(now - RETENTION_MS)
                updateNotification()
            }
        }
    }

    // --- Callbacks Socket.IO ----------------------------------------------------------------

    override fun onConnected() {
        log.ok("Connecté au serveur (WebSocket)")
        GatewayState.set { it.copy(link = GatewayState.Link.WEBSOCKET, lastError = null) }
        updateNotification()
        scope.launch {
            runCatching { sendHeartbeat() }
            requestFlush()
        }
    }

    override fun onDisconnected(reason: String) {
        log.warn("WebSocket déconnecté ($reason) : bascule en polling")
        GatewayState.set { it.copy(link = GatewayState.Link.POLLING) }
        updateNotification()
    }

    override fun onConnectError(message: String) {
        if (message.contains("invalid_token") || message.contains("missing_token")) {
            onRevoked("invalid_token")
            return
        }
        GatewayState.set { it.copy(lastError = message.ifBlank { null }) }
    }

    override fun onTasks(payload: JSONObject) {
        scope.launch {
            val messages = payload.optJSONArray("messages") ?: return@launch
            val ids = repo.enqueueTasks(messages)
            val ack = socket?.emitWithAck("task.ack", JSONObject().put("ids", org.json.JSONArray(ids)))
            if (ack == null) runCatching { app.api.ack(ids) }
            wake()
            requestFlush()
        }
    }

    override fun onCancel(payload: JSONObject) {
        scope.launch {
            payload.optJSONArray("ids")?.let { repo.cancel(it) }
            requestFlush()
        }
    }

    override fun onConfig(payload: JSONObject) {
        prefs.applyConfig(payload)
        log.info("Configuration reçue : 1 SMS / ${prefs.sendIntervalSec} s, ${prefs.maxPerHour}/h, ${prefs.maxPerDay}/j")
        wake()
    }

    override fun onRevoked(reason: String) {
        log.error("Appareil révoqué par le serveur ($reason) : réappairage nécessaire")
        prefs.clearPairing()
        notifyAlert(getString(R.string.alert_revoked_title), getString(R.string.alert_revoked_text))
        stopSelfCleanly()
    }

    // --- Utilitaires -----------------------------------------------------------------------------

    private fun handleNetworkError(context: String, e: Throwable) {
        if (e is ApiException && e.isUnauthorized) {
            onRevoked(e.errorCode)
            return
        }
        GatewayState.set { it.copy(lastError = "${context} : ${e.message ?: e.javaClass.simpleName}") }
    }

    private fun wake() { wakeups.trySend(Unit) }

    private suspend fun heartbeatPayload(): JSONObject {
        val (level, charging) = battery()
        return JSONObject()
            .put("battery", level)
            .put("charging", charging)
            .put("signal", signalLevel() ?: JSONObject.NULL)
            .put("local_queue_size", db.outgoing().queueSize())
            .put("app_version", BuildConfig.VERSION_NAME)
            .put("android_version", Build.VERSION.RELEASE)
            .put("sims", sims.toJson())
    }

    private fun battery(): Pair<Int, Boolean> {
        val intent = registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        val level = intent?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) ?: -1
        val scale = intent?.getIntExtra(BatteryManager.EXTRA_SCALE, 100) ?: 100
        val plugged = (intent?.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) ?: 0) != 0
        return (if (level >= 0 && scale > 0) level * 100 / scale else -1) to plugged
    }

    private fun signalLevel(): Int? = try {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            getSystemService(TelephonyManager::class.java)?.signalStrength?.level
        } else null
    } catch (e: Exception) {
        null
    }

    private fun startOfDay(): Long = Calendar.getInstance().apply {
        set(Calendar.HOUR_OF_DAY, 0); set(Calendar.MINUTE, 0); set(Calendar.SECOND, 0); set(Calendar.MILLISECOND, 0)
    }.timeInMillis

    @SuppressLint("WakelockTimeout")
    private fun acquireWakeLock() {
        // Garde le CPU actif écran éteint : indispensable pour une passerelle (téléphone de préférence en charge)
        wakeLock = getSystemService(PowerManager::class.java)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "SmsGateway::service")
            .apply { setReferenceCounted(false); acquire() }
    }

    private fun startInForeground(text: String) {
        val type = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            ServiceInfo.FOREGROUND_SERVICE_TYPE_REMOTE_MESSAGING
        } else 0
        ServiceCompat.startForeground(this, NOTIFICATION_ID, buildNotification(text), type)
    }

    private fun buildNotification(text: String): Notification {
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        return NotificationCompat.Builder(this, App.CHANNEL_SERVICE)
            .setSmallIcon(R.drawable.ic_stat_gateway)
            .setContentTitle(getString(R.string.notification_title, prefs.deviceName.ifBlank { getString(R.string.app_name) }))
            .setContentText(text)
            .setContentIntent(open)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .build()
    }

    private fun updateNotification() {
        val s = GatewayState.state.value
        val text = s.blockedReason ?: when (s.link) {
            GatewayState.Link.WEBSOCKET -> getString(R.string.status_online)
            GatewayState.Link.POLLING -> getString(R.string.status_polling)
            GatewayState.Link.CONNECTING -> getString(R.string.status_connecting)
            GatewayState.Link.OFFLINE -> getString(R.string.status_offline)
        }
        if (text == lastNotificationText) return
        lastNotificationText = text
        getSystemService(NotificationManager::class.java).notify(NOTIFICATION_ID, buildNotification(text))
    }

    private fun notifyAlert(title: String, text: String) {
        val open = PendingIntent.getActivity(this, 1, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val n = NotificationCompat.Builder(this, App.CHANNEL_ALERTS)
            .setSmallIcon(R.drawable.ic_stat_gateway)
            .setContentTitle(title).setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setContentIntent(open).setAutoCancel(true)
            .build()
        getSystemService(NotificationManager::class.java).notify(ALERT_ID, n)
    }

    private fun stopSelfCleanly() {
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    companion object {
        const val ACTION_START = "ma.smsgateway.START"
        const val ACTION_STOP = "ma.smsgateway.STOP"
        const val ACTION_RECONNECT = "ma.smsgateway.RECONNECT"

        private const val NOTIFICATION_ID = 42
        private const val ALERT_ID = 43
        private const val POLL_INTERVAL_MS = 15_000L
        private const val FLUSH_INTERVAL_MS = 30_000L
        private const val SENDING_TIMEOUT_MS = 3 * 60_000L
        private const val RETENTION_MS = 7 * 24 * 3600_000L
        private const val HOUR = 3600_000L

        @Volatile var running = false
            private set

        /** Signaux internes au processus (pas d'intent : évite les restrictions de démarrage en arrière-plan). */
        private val wakeups = Channel<Unit>(Channel.CONFLATED)
        private val flushRequests = Channel<Unit>(Channel.CONFLATED)

        fun requestFlush() { flushRequests.trySend(Unit) }

        /** Appelé par les récepteurs SMS : synchronise dès que possible. */
        fun flushSoon(context: Context) {
            requestFlush()
            wakeups.trySend(Unit)
            if (!running && App.from(context).prefs.isPaired) start(context)
        }

        fun start(context: Context, action: String = ACTION_START) {
            val intent = Intent(context, GatewayService::class.java).setAction(action)
            try {
                ContextCompat.startForegroundService(context, intent)
            } catch (e: Exception) {
                // Android 12+ : démarrage depuis l'arrière-plan refusé (optimisation batterie active)
                App.from(context).log.error("Démarrage du service refusé par le système", e)
            }
        }

        fun stop(context: Context) {
            if (!running) return
            runCatching { context.startService(Intent(context, GatewayService::class.java).setAction(ACTION_STOP)) }
        }
    }
}

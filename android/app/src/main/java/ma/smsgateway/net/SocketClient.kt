package ma.smsgateway.net

import io.socket.client.Ack
import io.socket.client.IO
import io.socket.client.Socket
import io.socket.engineio.client.transports.Polling
import io.socket.engineio.client.transports.WebSocket
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeoutOrNull
import okhttp3.OkHttpClient
import org.json.JSONObject
import java.net.URI
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume

/**
 * Connexion temps réel au namespace Socket.IO « /device ».
 * Reconnexion automatique (backoff 2 s → 30 s) ; l'authentification passe par `auth.token`.
 */
class SocketClient(
    private val serverUrl: String,
    private val token: String,
    private val http: OkHttpClient,
    private val listener: Listener,
) {
    interface Listener {
        fun onConnected()
        fun onDisconnected(reason: String)
        fun onConnectError(message: String)
        fun onTasks(payload: JSONObject)
        fun onCancel(payload: JSONObject)
        fun onConfig(payload: JSONObject)
        fun onRevoked(reason: String)
    }

    private var socket: Socket? = null

    /**
     * Client dédié au temps réel : pas de délai de lecture (la connexion reste ouverte en
     * permanence) et un ping WebSocket toutes les 20 s, qui garde la liaison active à travers
     * Traefik / Nginx et les NAT des opérateurs mobiles (sinon : « transport error »).
     */
    private val socketHttp: OkHttpClient = http.newBuilder()
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .writeTimeout(20, TimeUnit.SECONDS)
        .pingInterval(20, TimeUnit.SECONDS)
        .build()

    val isConnected: Boolean get() = socket?.connected() == true

    fun connect() {
        if (socket != null) return
        val opts = IO.Options().apply {
            path = "/socket.io"
            transports = arrayOf(WebSocket.NAME, Polling.NAME)
            reconnection = true
            reconnectionDelay = 2_000
            reconnectionDelayMax = 30_000
            timeout = 20_000
            auth = mapOf("token" to token)
            callFactory = socketHttp
            webSocketFactory = socketHttp
        }
        val s = IO.socket(URI.create("${serverUrl.trimEnd('/')}/device"), opts)
        s.on(Socket.EVENT_CONNECT) { listener.onConnected() }
        s.on(Socket.EVENT_DISCONNECT) { args -> listener.onDisconnected(args.firstOrNull()?.toString() ?: "") }
        s.on(Socket.EVENT_CONNECT_ERROR) { args ->
            val msg = (args.firstOrNull() as? JSONObject)?.optString("message") ?: args.firstOrNull()?.toString() ?: ""
            listener.onConnectError(msg)
        }
        s.on("task.send") { args -> (args.firstOrNull() as? JSONObject)?.let(listener::onTasks) }
        s.on("task.cancel") { args -> (args.firstOrNull() as? JSONObject)?.let(listener::onCancel) }
        s.on("config.update") { args -> (args.firstOrNull() as? JSONObject)?.let(listener::onConfig) }
        s.on("device.revoked") { args ->
            listener.onRevoked((args.firstOrNull() as? JSONObject)?.optString("reason") ?: "revoked")
        }
        socket = s
        s.connect()
    }

    /** Émet un événement et attend l'ACK du serveur (null si déconnecté ou délai dépassé). */
    suspend fun emitWithAck(event: String, payload: JSONObject, timeoutMs: Long = 15_000): JSONObject? {
        val s = socket ?: return null
        if (!s.connected()) return null
        return withTimeoutOrNull(timeoutMs) {
            suspendCancellableCoroutine { cont ->
                s.emit(event, arrayOf(payload), Ack { args ->
                    if (cont.isActive) cont.resume(args.firstOrNull() as? JSONObject ?: JSONObject())
                })
            }
        }
    }

    fun disconnect() {
        socket?.off()
        socket?.disconnect()
        socket = null
    }
}

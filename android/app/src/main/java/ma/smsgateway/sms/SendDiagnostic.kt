package ma.smsgateway.sms

import android.app.Activity
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.delay
import kotlinx.coroutines.withTimeoutOrNull
import ma.smsgateway.App
import java.util.UUID

/**
 * Diagnostic d'envoi : essaie successivement plusieurs façons d'appeler SmsManager vers un
 * numéro de test, s'arrête à la première qui passe et la mémorise pour tous les envois.
 * Utile quand le modem renvoie MODEM_ERROR (resultCode 111) alors que l'application
 * Messages du téléphone envoie sans problème.
 */
class SendDiagnostic(private val context: Context) {

    data class Step(val index: Int, val total: Int, val options: SendOptions, val ok: Boolean, val detail: String)

    private val app = App.from(context)

    val variants: List<SendOptions> = listOf(
        SendOptions(deliveryReports = false),
        SendOptions(deliveryReports = false, nationalFormat = true),
        SendOptions(deliveryReports = false, defaultManager = true),
        SendOptions(deliveryReports = false, nationalFormat = true, defaultManager = true),
        SendOptions(deliveryReports = false, nationalFormat = true, forceMultipart = true),
        SendOptions(deliveryReports = false, nationalFormat = true, defaultManager = true, forceMultipart = true),
    )

    /** Retourne la variante qui fonctionne (déjà enregistrée), ou null si aucune ne passe. */
    suspend fun run(phone: String, slot: Int, onStep: (Step) -> Unit): SendOptions? {
        val sender = SmsSender(context, SimManager(context))
        app.log.info("Diagnostic d'envoi vers $phone (SIM ${slot + 1})")
        variants.forEachIndexed { i, options ->
            val (ok, detail) = attempt(sender, phone, slot, options, "Test SMS Gateway ${i + 1}/${variants.size}")
            onStep(Step(i + 1, variants.size, options, ok, detail))
            app.log.info("Diagnostic ${i + 1}/${variants.size} (${options.describe()}) : ${if (ok) "OK" else detail}")
            if (ok) {
                app.prefs.sendNationalFormat = options.nationalFormat
                app.prefs.sendDefaultManager = options.defaultManager
                app.prefs.sendForceMultipart = options.forceMultipart
                app.log.ok("Variante d'envoi enregistrée : ${options.describe()}")
                return options
            }
            delay(3_000)  // laisse le modem se remettre entre deux essais
        }
        app.log.error("Diagnostic : aucune variante n'a fonctionné")
        return null
    }

    /** Envoie un SMS de test et attend l'accusé d'envoi (resultCode). */
    private suspend fun attempt(sender: SmsSender, phone: String, slot: Int, options: SendOptions,
                                body: String): Pair<Boolean, String> {
        val action = "ma.smsgateway.DIAG_SENT_" + UUID.randomUUID().toString().take(8)
        val result = CompletableDeferred<Pair<Int, Int>>()
        val receiver = object : BroadcastReceiver() {
            override fun onReceive(ctx: Context, intent: Intent) {
                result.complete(resultCode to intent.getIntExtra("errorCode", -1))
            }
        }
        ContextCompat.registerReceiver(context, receiver, IntentFilter(action), ContextCompat.RECEIVER_NOT_EXPORTED)
        try {
            val submitted = sender.sendRaw(phone, body, slot, options) { act, part, _ ->
                // Seul l'accusé d'envoi du premier segment est observé (intent explicite au paquet)
                val intent = Intent(if (act == ma.smsgateway.receiver.SmsStatusReceiver.ACTION_SENT && part == 0)
                    action else "$action.ignore").setPackage(context.packageName)
                PendingIntent.getBroadcast(context, (action + act + part).hashCode(), intent,
                    PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            }
            if (submitted is SmsSender.Result.Failed) return false to "${submitted.errorCode} (${submitted.message})"
            val (code, ril) = withTimeoutOrNull(45_000) { result.await() } ?: return false to "pas de réponse du modem"
            if (code == Activity.RESULT_OK) return true to "OK"
            return false to "${ErrorCodes.fromSentResult(code)} (resultCode=$code" + (if (ril >= 0) ", errorCode=$ril" else "") + ")"
        } finally {
            runCatching { context.unregisterReceiver(receiver) }
        }
    }

    companion object {
        /** 06…/07…/212…/+212… -> +212…, sinon inchangé. */
        fun normalize(input: String): String {
            val digits = input.filter { it.isDigit() || it == '+' }
            return when {
                digits.startsWith("+") -> digits
                digits.startsWith("00") -> "+" + digits.drop(2)
                digits.startsWith("212") -> "+$digits"
                digits.startsWith("0") && digits.length == 10 -> "+212" + digits.drop(1)
                else -> digits
            }
        }
    }
}

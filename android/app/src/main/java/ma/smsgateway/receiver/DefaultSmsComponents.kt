package ma.smsgateway.receiver

import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.IBinder
import android.provider.Telephony
import android.telephony.SmsManager
import android.telephony.SubscriptionManager
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import ma.smsgateway.App
import ma.smsgateway.R
import ma.smsgateway.sms.DefaultSmsApp

/*
 * Composants exigés par Android pour pouvoir devenir l'application SMS par défaut.
 * La transmission au serveur reste assurée par SmsReceiver (SMS_RECEIVED, reçu dans tous les cas) :
 * ces composants se contentent de garder l'historique du téléphone à jour.
 */

/** SMS entrant (uniquement quand l'application est celle par défaut) : écrit dans la boîte de réception. */
class SmsDeliverReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Telephony.Sms.Intents.SMS_DELIVER_ACTION) return
        val parts = Telephony.Sms.Intents.getMessagesFromIntent(intent) ?: return
        val subId = intent.getIntExtra(SubscriptionManager.EXTRA_SUBSCRIPTION_INDEX, intent.getIntExtra("subscription", -1))
        parts.groupBy { it.displayOriginatingAddress ?: it.originatingAddress ?: "" }.forEach { (sender, msgs) ->
            val body = msgs.joinToString("") { it.displayMessageBody ?: it.messageBody ?: "" }
            val sentAt = msgs.minOf { it.timestampMillis }
            DefaultSmsApp.writeInbox(context, sender, body, sentAt, subId)
            notify(context, sender, body)
        }
    }

    private fun notify(context: Context, sender: String, body: String) {
        val open = context.packageManager.getLaunchIntentForPackage(context.packageName)
        val notification = NotificationCompat.Builder(context, App.CHANNEL_ALERTS)
            .setSmallIcon(R.drawable.ic_stat_gateway)
            .setContentTitle(sender)
            .setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setAutoCancel(true)
            .apply {
                if (open != null) setContentIntent(PendingIntent.getActivity(context, 0, open, PendingIntent.FLAG_IMMUTABLE))
            }
            .build()
        runCatching { NotificationManagerCompat.from(context).notify((sender + body).hashCode(), notification) }
    }
}

/** MMS entrants : non pris en charge par la passerelle (composant requis uniquement). */
class MmsReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) = Unit
}

/** « Répondre par message » depuis l'écran d'appel entrant. */
class RespondViaMessageService : Service() {
    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val phone = intent?.data?.schemeSpecificPart?.substringBefore('?')
        val text = intent?.getStringExtra(Intent.EXTRA_TEXT)
        if (!phone.isNullOrBlank() && !text.isNullOrBlank()) {
            runCatching {
                @Suppress("DEPRECATION")
                val manager = SmsManager.getDefault()
                manager.sendMultipartTextMessage(phone, null, manager.divideMessage(text), null, null)
                DefaultSmsApp.writeSent(this, phone, text, null)
            }
        }
        stopSelf(startId)
        return START_NOT_STICKY
    }
}

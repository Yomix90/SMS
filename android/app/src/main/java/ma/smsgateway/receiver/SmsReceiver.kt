package ma.smsgateway.receiver

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.provider.Telephony
import android.telephony.SubscriptionManager
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import ma.smsgateway.App
import ma.smsgateway.data.IncomingSms
import ma.smsgateway.service.GatewayRepository
import ma.smsgateway.service.GatewayService
import ma.smsgateway.sms.SimManager
import java.util.UUID

/**
 * SMS entrants : les segments d'un SMS long sont réassemblés, le message est stocké
 * en base (Room) puis transmis immédiatement au serveur (ou dès la reconnexion).
 */
class SmsReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Telephony.Sms.Intents.SMS_RECEIVED_ACTION) return
        if (!App.from(context).prefs.isPaired) return
        val parts = Telephony.Sms.Intents.getMessagesFromIntent(intent) ?: return
        if (parts.isEmpty()) return

        val subId = intent.getIntExtra(SubscriptionManager.EXTRA_SUBSCRIPTION_INDEX,
            intent.getIntExtra("subscription", -1))
        val slot = if (subId >= 0) SimManager(context).slotForSubscription(subId) else null

        // Un même broadcast peut contenir plusieurs messages d'expéditeurs différents
        val grouped = parts.groupBy { it.displayOriginatingAddress ?: it.originatingAddress ?: "" }
        val pending = goAsync()
        scope.launch {
            try {
                val repo = GatewayRepository(context)
                grouped.forEach { (sender, msgs) ->
                    val body = msgs.joinToString("") { it.displayMessageBody ?: it.messageBody ?: "" }
                    val timestamp = msgs.minOf { it.timestampMillis }.takeIf { it > 0 } ?: System.currentTimeMillis()
                    repo.storeIncoming(IncomingSms(uuid = UUID.randomUUID().toString(), phone = sender, body = body,
                        receivedAt = timestamp, simSlot = slot))
                }
                GatewayService.flushSoon(context)
            } finally {
                pending.finish()
            }
        }
    }

    private companion object {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    }
}

package ma.smsgateway.receiver

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.telephony.SmsMessage
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import ma.smsgateway.service.GatewayRepository
import ma.smsgateway.service.GatewayService
import ma.smsgateway.sms.ErrorCodes

/** Reçoit les accusés d'envoi (resultCode) et de livraison (PDU de statut) de chaque segment. */
class SmsStatusReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        val id = intent.getStringExtra(EXTRA_ID) ?: return
        val total = intent.getIntExtra(EXTRA_TOTAL, 1)
        val resultCode = resultCode
        val pending = goAsync()
        scope.launch {
            try {
                val repo = GatewayRepository(context)
                when (intent.action) {
                    ACTION_SENT -> repo.onPartSent(id, total, ErrorCodes.fromSentResult(resultCode))
                    ACTION_DELIVERED -> {
                        val status = deliveryStatus(intent)
                        // 32..63 : l'opérateur réessaie encore, on attend l'accusé final
                        if (status == null || status !in 32..63) {
                            repo.onPartDelivered(id, total, failed = status != null && status >= 64)
                        }
                    }
                }
                GatewayService.flushSoon(context)
            } finally {
                pending.finish()
            }
        }
    }

    /** TP-Status (GSM 03.40) : 0-31 livré, 32-63 en cours, ≥ 64 échec. null si illisible. */
    private fun deliveryStatus(intent: Intent): Int? {
        val pdu = intent.getByteArrayExtra("pdu") ?: return null
        val format = intent.getStringExtra("format") ?: "3gpp"
        return try {
            SmsMessage.createFromPdu(pdu, format)?.status
        } catch (e: Exception) {
            null
        }
    }

    companion object {
        const val ACTION_SENT = "ma.smsgateway.SMS_SENT"
        const val ACTION_DELIVERED = "ma.smsgateway.SMS_DELIVERED"
        const val EXTRA_ID = "message_id"
        const val EXTRA_PART = "part"
        const val EXTRA_TOTAL = "total"
        private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    }
}

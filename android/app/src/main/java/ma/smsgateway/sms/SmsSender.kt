package ma.smsgateway.sms

import android.Manifest
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.telephony.SmsManager
import androidx.core.content.ContextCompat
import ma.smsgateway.data.OutgoingSms
import ma.smsgateway.receiver.SmsStatusReceiver

/**
 * Envoi via SmsManager : découpage multipart (GSM-7 / UCS-2 géré par Android, donc l'arabe aussi),
 * choix de la SIM, et un PendingIntent par segment pour les accusés d'envoi et de livraison.
 */
class SmsSender(private val context: Context, private val sims: SimManager) {

    sealed class Result {
        data class Submitted(val parts: Int, val simSlot: Int?) : Result()
        data class Failed(val errorCode: String, val message: String) : Result()
    }

    fun send(sms: OutgoingSms, defaultSlot: Int): Result {
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.SEND_SMS) != PackageManager.PERMISSION_GRANTED) {
            return Result.Failed(ErrorCodes.PERMISSION_DENIED, "Permission SEND_SMS refusée")
        }
        val slot = sms.simSlot ?: defaultSlot
        val activeSims = sims.activeSims()
        if (activeSims.isEmpty() && hasPhoneStatePermission()) {
            return Result.Failed(ErrorCodes.NO_SIM, "Aucune carte SIM active")
        }
        // SIM demandée absente : on retombe sur la première SIM active plutôt que d'échouer
        val subId = sims.subscriptionIdForSlot(slot) ?: activeSims.firstOrNull()?.subscriptionId
        val usedSlot = activeSims.firstOrNull { it.subscriptionId == subId }?.slot ?: slot

        return try {
            val manager = managerFor(subId)
            val parts = manager.divideMessage(sms.body)
            val sentIntents = ArrayList<PendingIntent>(parts.size)
            val deliveredIntents = ArrayList<PendingIntent>(parts.size)
            parts.indices.forEach { i ->
                sentIntents += pending(SmsStatusReceiver.ACTION_SENT, sms.id, i, parts.size)
                deliveredIntents += pending(SmsStatusReceiver.ACTION_DELIVERED, sms.id, i, parts.size)
            }
            if (parts.size == 1) {
                manager.sendTextMessage(sms.phone, null, sms.body, sentIntents[0], deliveredIntents[0])
            } else {
                manager.sendMultipartTextMessage(sms.phone, null, parts, sentIntents, deliveredIntents)
            }
            Result.Submitted(parts.size, usedSlot)
        } catch (e: SecurityException) {
            Result.Failed(ErrorCodes.PERMISSION_DENIED, e.message ?: "SecurityException")
        } catch (e: IllegalArgumentException) {
            Result.Failed(ErrorCodes.INVALID_NUMBER, e.message ?: "Numéro ou texte invalide")
        } catch (e: Exception) {
            Result.Failed(ErrorCodes.GENERIC_FAILURE, e.message ?: e.javaClass.simpleName)
        }
    }

    private fun hasPhoneStatePermission() =
        ContextCompat.checkSelfPermission(context, Manifest.permission.READ_PHONE_STATE) == PackageManager.PERMISSION_GRANTED

    @Suppress("DEPRECATION")
    private fun managerFor(subId: Int?): SmsManager {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            val base = context.getSystemService(SmsManager::class.java)
            if (subId != null) base.createForSubscriptionId(subId) else base
        } else {
            if (subId != null) SmsManager.getSmsManagerForSubscriptionId(subId) else SmsManager.getDefault()
        }
    }

    private fun pending(action: String, id: String, part: Int, total: Int): PendingIntent {
        val intent = Intent(context, SmsStatusReceiver::class.java).apply {
            this.action = action
            putExtra(SmsStatusReceiver.EXTRA_ID, id)
            putExtra(SmsStatusReceiver.EXTRA_PART, part)
            putExtra(SmsStatusReceiver.EXTRA_TOTAL, total)
        }
        // Code de requête unique par message / segment / type d'accusé
        val requestCode = (id + action + part).hashCode()
        // MUTABLE : le système ajoute des extras (PDU de l'accusé de livraison, code d'erreur)
        val flags = PendingIntent.FLAG_UPDATE_CURRENT or
            (if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) PendingIntent.FLAG_MUTABLE else 0)
        return PendingIntent.getBroadcast(context, requestCode, intent, flags)
    }
}

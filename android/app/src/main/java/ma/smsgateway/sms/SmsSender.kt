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
 * Manière d'appeler SmsManager. Certains modems / ROM (Xiaomi notamment) refusent une variante
 * (resultCode 111 = RIL_MODEM_ERR) alors qu'une autre passe : le diagnostic d'envoi teste ces
 * variantes et mémorise celle qui fonctionne (voir SendDiagnostic).
 */
data class SendOptions(
    val deliveryReports: Boolean = true,
    /** Numéro marocain au format national (0612345678) au lieu de +212612345678. */
    val nationalFormat: Boolean = false,
    /** Gestionnaire SMS par défaut du téléphone au lieu de celui de la SIM choisie. */
    val defaultManager: Boolean = false,
    /** Toujours passer par sendMultipartTextMessage, même pour un seul segment. */
    val forceMultipart: Boolean = false,
) {
    fun describe(): String = buildList {
        add(if (nationalFormat) "numéro 06…" else "numéro +212…")
        add(if (defaultManager) "gestionnaire par défaut" else "gestionnaire de la SIM")
        add(if (forceMultipart) "multipart" else "simple")
        add(if (deliveryReports) "avec accusé" else "sans accusé")
    }.joinToString(", ")
}

/**
 * Envoi via SmsManager : découpage multipart (GSM-7 / UCS-2 géré par Android, donc l'arabe aussi),
 * choix de la SIM, et un PendingIntent par segment pour les accusés d'envoi et de livraison.
 */
class SmsSender(private val context: Context, private val sims: SimManager) {

    sealed class Result {
        data class Submitted(val parts: Int, val simSlot: Int?) : Result()
        data class Failed(val errorCode: String, val message: String) : Result()
    }

    fun send(sms: OutgoingSms, defaultSlot: Int, options: SendOptions): Result {
        val opts = options.copy(deliveryReports = options.deliveryReports && sms.withDeliveryReport)
        return sendRaw(sms.phone, sms.body, sms.simSlot ?: defaultSlot, opts) { action, part, total ->
            statusIntent(action, sms.id, part, total)
        }
    }

    /**
     * Envoi bas niveau, partagé avec le diagnostic.
     * [intentFor] fournit le PendingIntent de chaque accusé (action, segment, total).
     */
    fun sendRaw(phone: String, body: String, slot: Int, options: SendOptions,
                intentFor: (action: String, part: Int, total: Int) -> PendingIntent): Result {
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.SEND_SMS) != PackageManager.PERMISSION_GRANTED) {
            return Result.Failed(ErrorCodes.PERMISSION_DENIED, "Permission SEND_SMS refusée")
        }
        val activeSims = sims.activeSims()
        if (activeSims.isEmpty() && hasPhoneStatePermission()) {
            return Result.Failed(ErrorCodes.NO_SIM, "Aucune carte SIM active")
        }
        // SIM demandée absente : on retombe sur la première SIM active plutôt que d'échouer
        val subId = sims.subscriptionIdForSlot(slot) ?: activeSims.firstOrNull()?.subscriptionId
        val usedSlot = activeSims.firstOrNull { it.subscriptionId == subId }?.slot ?: slot
        val destination = formatNumber(phone, options.nationalFormat)

        return try {
            val manager = managerFor(if (options.defaultManager) null else subId)
            val parts = manager.divideMessage(body)
            val sentIntents = ArrayList<PendingIntent>(parts.size)
            val deliveredIntents = ArrayList<PendingIntent>(parts.size)
            parts.indices.forEach { i ->
                sentIntents += intentFor(SmsStatusReceiver.ACTION_SENT, i, parts.size)
                if (options.deliveryReports) deliveredIntents += intentFor(SmsStatusReceiver.ACTION_DELIVERED, i, parts.size)
            }
            if (parts.size == 1 && !options.forceMultipart) {
                manager.sendTextMessage(destination, null, body, sentIntents[0], deliveredIntents.firstOrNull())
            } else {
                manager.sendMultipartTextMessage(destination, null, parts, sentIntents,
                    if (options.deliveryReports) deliveredIntents else null)
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

    private fun statusIntent(action: String, id: String, part: Int, total: Int): PendingIntent {
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

    companion object {
        /** +212612345678 -> 0612345678 si demandé ; les autres numéros restent inchangés. */
        fun formatNumber(phone: String, national: Boolean): String =
            if (national && phone.startsWith("+212") && phone.length == 13) "0" + phone.substring(4) else phone
    }
}

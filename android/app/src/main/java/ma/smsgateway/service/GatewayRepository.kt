package ma.smsgateway.service

import android.content.Context
import ma.smsgateway.App
import ma.smsgateway.data.IncomingSms
import ma.smsgateway.data.OutStatus
import ma.smsgateway.data.OutgoingSms
import ma.smsgateway.data.StatusReport
import org.json.JSONArray
import org.json.JSONObject

/**
 * Logique de la file locale, partagée par le service et les récepteurs de broadcast.
 * Toute transition de statut crée une ligne dans la boîte d'envoi des statuts (status_outbox),
 * vidée vers le serveur dès qu'une connexion est disponible.
 */
class GatewayRepository(context: Context) {
    private val app = App.from(context)
    private val db = app.db
    private val log = app.log

    /** Lot reçu du serveur (« task.send » ou GET /tasks). Retourne les ids à acquitter. */
    suspend fun enqueueTasks(messages: JSONArray): List<String> {
        val ids = mutableListOf<String>()
        for (i in 0 until messages.length()) {
            val m = messages.optJSONObject(i) ?: continue
            val id = m.optString("id").takeIf { it.isNotBlank() } ?: continue
            val sms = OutgoingSms(
                id = id,
                phone = m.optString("phone"),
                body = m.optString("body"),
                simSlot = if (m.isNull("sim_slot")) null else m.optInt("sim_slot"),
                priority = m.optInt("priority", 0),
            )
            val inserted = db.outgoing().insert(sms) != -1L
            if (inserted) {
                log.info("Tâche reçue → ${sms.phone}")
            } else {
                // Déjà connu (renvoi après coupure) : on ré-annonce le statut final éventuel
                db.outgoing().get(id)?.let { existing ->
                    if (existing.status in FINAL_REPORTABLE) report(existing.id, existing.status, existing.errorCode)
                }
            }
            ids += id
        }
        return ids
    }

    suspend fun cancel(ids: JSONArray) {
        for (i in 0 until ids.length()) {
            val id = ids.optString(i)
            if (db.outgoing().cancelIfPending(id) > 0) {
                report(id, OutStatus.CANCELED)
                log.warn("Envoi annulé par le serveur")
            }
        }
    }

    suspend fun report(messageId: String, status: String, errorCode: String? = null, errorMessage: String? = null,
                       simSlot: Int? = null, parts: Int? = null) {
        db.statuses().insert(StatusReport(messageId = messageId, status = status, errorCode = errorCode,
            errorMessage = errorMessage, simSlot = simSlot, parts = parts))
    }

    /** Accusé d'envoi d'un segment. */
    suspend fun onPartSent(id: String, total: Int, errorCode: String?) {
        val sms = db.outgoing().get(id) ?: return
        if (sms.status != OutStatus.SENDING) return
        if (errorCode != null) {
            db.outgoing().setStatus(id, OutStatus.FAILED, errorCode)
            report(id, "failed", errorCode, simSlot = sms.simSlot)
            log.error("Échec d'envoi vers ${sms.phone} ($errorCode)")
            return
        }
        db.outgoing().incPartsSent(id)
        val updated = db.outgoing().get(id) ?: return
        if (updated.partsSent >= total) {
            db.outgoing().setStatus(id, OutStatus.SENT, sentAt = System.currentTimeMillis())
            report(id, "sent", simSlot = sms.simSlot, parts = total)
            log.outgoing("Envoyé → ${sms.phone}" + if (total > 1) " ($total SMS)" else "")
        }
    }

    /** Accusé de livraison d'un segment (status = TP-Status GSM). */
    suspend fun onPartDelivered(id: String, total: Int, failed: Boolean) {
        val sms = db.outgoing().get(id) ?: return
        if (sms.status == OutStatus.DELIVERED || sms.status == OutStatus.FAILED) return
        if (failed) {
            db.outgoing().setStatus(id, OutStatus.FAILED, "DELIVERY_FAILED")
            report(id, "failed", "DELIVERY_FAILED", "Accusé de livraison négatif", sms.simSlot)
            log.error("Non livré : ${sms.phone}")
            return
        }
        db.outgoing().incPartsDelivered(id)
        val updated = db.outgoing().get(id) ?: return
        if (updated.partsDelivered >= total) {
            db.outgoing().setStatus(id, OutStatus.DELIVERED, sentAt = System.currentTimeMillis())
            report(id, "delivered", simSlot = sms.simSlot, parts = total)
            log.ok("Livré → ${sms.phone}")
        }
    }

    suspend fun storeIncoming(sms: IncomingSms) {
        db.incoming().insert(sms)
        log.incoming("Reçu de ${sms.phone} : ${sms.body.take(60)}")
    }

    // --- Sérialisation pour le serveur ---------------------------------------------------

    fun reportsToJson(reports: List<StatusReport>): JSONArray = JSONArray().apply {
        reports.forEach { r ->
            put(JSONObject().put("id", r.messageId).put("status", r.status).put("timestamp", r.timestamp)
                .put("error_code", r.errorCode ?: JSONObject.NULL)
                .put("error_message", r.errorMessage ?: JSONObject.NULL)
                .put("sim_slot", r.simSlot ?: JSONObject.NULL)
                .put("parts", r.parts ?: JSONObject.NULL))
        }
    }

    fun incomingToJson(items: List<IncomingSms>): JSONArray = JSONArray().apply {
        items.forEach { m ->
            put(JSONObject().put("uuid", m.uuid).put("phone", m.phone).put("body", m.body)
                .put("received_at", m.receivedAt).put("sim_slot", m.simSlot ?: JSONObject.NULL))
        }
    }

    private companion object {
        val FINAL_REPORTABLE = setOf(OutStatus.SENT, OutStatus.DELIVERED, OutStatus.FAILED, OutStatus.CANCELED)
    }
}

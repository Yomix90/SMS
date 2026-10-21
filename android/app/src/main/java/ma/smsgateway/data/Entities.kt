package ma.smsgateway.data

import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey

/** Statuts locaux d'un SMS sortant. */
object OutStatus {
    const val PENDING = "pending"     // reçu du serveur, en attente du limiteur
    const val SENDING = "sending"     // remis à SmsManager, accusé d'envoi attendu
    const val SENT = "sent"
    const val DELIVERED = "delivered"
    const val FAILED = "failed"
    const val CANCELED = "canceled"
}

/** SMS à envoyer (file locale : survit aux coupures réseau et aux redémarrages). */
@Entity(tableName = "outgoing", indices = [Index("status"), Index("sentAt")])
data class OutgoingSms(
    @PrimaryKey val id: String,          // UUID du message côté serveur
    val phone: String,
    val body: String,
    val simSlot: Int?,
    val priority: Int = 0,
    val status: String = OutStatus.PENDING,
    val partsTotal: Int = 0,
    val partsSent: Int = 0,
    val partsDelivered: Int = 0,
    val errorCode: String? = null,
    val createdAt: Long = System.currentTimeMillis(),
    val sendingAt: Long? = null,
    val sentAt: Long? = null,
)

/** Boîte d'envoi des statuts à remonter au serveur (rejouée à la reconnexion). */
@Entity(tableName = "status_outbox")
data class StatusReport(
    @PrimaryKey(autoGenerate = true) val rowId: Long = 0,
    val messageId: String,
    val status: String,                  // sent | delivered | failed | canceled
    val errorCode: String? = null,
    val errorMessage: String? = null,
    val simSlot: Int? = null,
    val parts: Int? = null,
    val timestamp: Long = System.currentTimeMillis(),
)

/** SMS reçu, conservé jusqu'à confirmation par le serveur. */
@Entity(tableName = "incoming", indices = [Index("synced")])
data class IncomingSms(
    @PrimaryKey val uuid: String,
    val phone: String,
    val body: String,
    val receivedAt: Long,
    val simSlot: Int?,
    val synced: Boolean = false,
)

/** Journal affiché sur l'écran principal. */
@Entity(tableName = "log")
data class LogEntry(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val time: Long = System.currentTimeMillis(),
    val level: String,                   // info | ok | warn | error | in | out
    val text: String,
)

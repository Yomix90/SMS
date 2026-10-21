package ma.smsgateway.data

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import kotlinx.coroutines.flow.Flow

@Dao
interface OutgoingDao {
    /** Ignore les doublons : le serveur peut renvoyer un lot déjà reçu après une coupure. */
    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insert(sms: OutgoingSms): Long

    @Query("SELECT * FROM outgoing WHERE id = :id")
    suspend fun get(id: String): OutgoingSms?

    @Query("SELECT * FROM outgoing WHERE status = 'pending' ORDER BY priority DESC, createdAt LIMIT 1")
    suspend fun nextPending(): OutgoingSms?

    @Query("SELECT COUNT(*) FROM outgoing WHERE status IN ('pending', 'sending')")
    suspend fun queueSize(): Int

    @Query("SELECT COUNT(*) FROM outgoing WHERE status IN ('pending', 'sending')")
    fun queueSizeFlow(): Flow<Int>

    /** SMS remis à l'opérateur depuis un instant (pour le limiteur heure / jour). */
    @Query("SELECT COUNT(*) FROM outgoing WHERE sendingAt >= :since")
    suspend fun countSendingSince(since: Long): Int

    @Query("SELECT COUNT(*) FROM outgoing WHERE status IN ('sent', 'delivered') AND sentAt >= :since")
    fun sentSinceFlow(since: Long): Flow<Int>

    @Query("SELECT MAX(sendingAt) FROM outgoing")
    suspend fun lastSendingAt(): Long?

    @Query("UPDATE outgoing SET status = 'sending', sendingAt = :now, partsTotal = :parts, partsSent = 0, partsDelivered = 0, errorCode = NULL WHERE id = :id")
    suspend fun markSending(id: String, parts: Int, now: Long = System.currentTimeMillis())

    @Query("UPDATE outgoing SET partsSent = partsSent + 1 WHERE id = :id")
    suspend fun incPartsSent(id: String)

    @Query("UPDATE outgoing SET partsDelivered = partsDelivered + 1 WHERE id = :id")
    suspend fun incPartsDelivered(id: String)

    @Query("UPDATE outgoing SET status = :status, errorCode = :error, sentAt = COALESCE(sentAt, :sentAt) WHERE id = :id")
    suspend fun setStatus(id: String, status: String, error: String? = null, sentAt: Long? = null)

    /** Envois restés sans accusé (téléphone redémarré, radio bloquée…). */
    @Query("SELECT * FROM outgoing WHERE status = 'sending' AND sendingAt < :before")
    suspend fun stuckSending(before: Long): List<OutgoingSms>

    @Query("UPDATE outgoing SET status = 'canceled' WHERE id = :id AND status = 'pending'")
    suspend fun cancelIfPending(id: String): Int

    @Query("DELETE FROM outgoing WHERE status IN ('delivered', 'failed', 'canceled', 'sent') AND createdAt < :before")
    suspend fun purgeOld(before: Long)
}

@Dao
interface StatusDao {
    @Insert
    suspend fun insert(report: StatusReport)

    @Query("SELECT * FROM status_outbox ORDER BY rowId LIMIT :limit")
    suspend fun batch(limit: Int = 100): List<StatusReport>

    @Query("DELETE FROM status_outbox WHERE rowId IN (:ids)")
    suspend fun delete(ids: List<Long>)

    @Query("SELECT COUNT(*) FROM status_outbox")
    suspend fun count(): Int
}

@Dao
interface IncomingDao {
    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insert(sms: IncomingSms)

    @Query("SELECT * FROM incoming WHERE synced = 0 ORDER BY receivedAt LIMIT :limit")
    suspend fun unsynced(limit: Int = 100): List<IncomingSms>

    @Query("UPDATE incoming SET synced = 1 WHERE uuid IN (:ids)")
    suspend fun markSynced(ids: List<String>)

    @Query("SELECT COUNT(*) FROM incoming WHERE receivedAt >= :since")
    fun receivedSinceFlow(since: Long): Flow<Int>

    @Query("DELETE FROM incoming WHERE synced = 1 AND receivedAt < :before")
    suspend fun purgeOld(before: Long)
}

@Dao
interface LogDao {
    @Insert
    suspend fun insert(entry: LogEntry)

    @Query("SELECT * FROM log ORDER BY id DESC LIMIT 200")
    fun latest(): Flow<List<LogEntry>>

    @Query("DELETE FROM log WHERE id NOT IN (SELECT id FROM log ORDER BY id DESC LIMIT 500)")
    suspend fun trim()
}

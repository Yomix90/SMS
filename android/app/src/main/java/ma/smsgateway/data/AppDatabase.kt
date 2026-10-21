package ma.smsgateway.data

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase

@Database(
    entities = [OutgoingSms::class, StatusReport::class, IncomingSms::class, LogEntry::class],
    version = 1,
    exportSchema = false,
)
abstract class AppDatabase : RoomDatabase() {
    abstract fun outgoing(): OutgoingDao
    abstract fun statuses(): StatusDao
    abstract fun incoming(): IncomingDao
    abstract fun logs(): LogDao

    companion object {
        @Volatile private var instance: AppDatabase? = null

        fun get(context: Context): AppDatabase = instance ?: synchronized(this) {
            instance ?: Room.databaseBuilder(context.applicationContext, AppDatabase::class.java, "gateway.db")
                .fallbackToDestructiveMigration()
                .build()
                .also { instance = it }
        }
    }
}

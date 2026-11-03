package ma.smsgateway.data

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase

@Database(
    entities = [OutgoingSms::class, StatusReport::class, IncomingSms::class, LogEntry::class],
    version = 2,
    exportSchema = false,
)
abstract class AppDatabase : RoomDatabase() {
    abstract fun outgoing(): OutgoingDao
    abstract fun statuses(): StatusDao
    abstract fun incoming(): IncomingDao
    abstract fun logs(): LogDao

    companion object {
        @Volatile private var instance: AppDatabase? = null

        /** v2 : nombre de tentatives et accusé de livraison par message. */
        private val MIGRATION_1_2 = object : Migration(1, 2) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE outgoing ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
                db.execSQL("ALTER TABLE outgoing ADD COLUMN withDeliveryReport INTEGER NOT NULL DEFAULT 1")
            }
        }

        fun get(context: Context): AppDatabase = instance ?: synchronized(this) {
            instance ?: Room.databaseBuilder(context.applicationContext, AppDatabase::class.java, "gateway.db")
                .addMigrations(MIGRATION_1_2)
                .fallbackToDestructiveMigration()
                .build()
                .also { instance = it }
        }
    }
}

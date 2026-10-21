package ma.smsgateway

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import ma.smsgateway.data.AppDatabase
import ma.smsgateway.data.GatewayLog
import ma.smsgateway.data.Prefs
import ma.smsgateway.net.ApiClient
import ma.smsgateway.work.WatchdogWorker

class App : Application() {

    lateinit var prefs: Prefs
        private set
    lateinit var db: AppDatabase
        private set
    lateinit var log: GatewayLog
        private set
    lateinit var api: ApiClient
        private set

    override fun onCreate() {
        super.onCreate()
        instance = this
        prefs = Prefs(this)
        db = AppDatabase.get(this)
        log = GatewayLog(db.logs())
        api = ApiClient(prefs)
        createChannels()
        if (prefs.isPaired) WatchdogWorker.schedule(this)
    }

    private fun createChannels() {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL_SERVICE, getString(R.string.channel_service), NotificationManager.IMPORTANCE_LOW)
                .apply { setShowBadge(false) }
        )
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL_ALERTS, getString(R.string.channel_alerts), NotificationManager.IMPORTANCE_HIGH)
        )
    }

    companion object {
        const val CHANNEL_SERVICE = "gateway_service"
        const val CHANNEL_ALERTS = "gateway_alerts"

        lateinit var instance: App
            private set

        fun from(context: Context): App = context.applicationContext as App
    }
}

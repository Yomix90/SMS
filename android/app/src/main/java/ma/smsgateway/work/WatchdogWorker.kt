package ma.smsgateway.work

import android.content.Context
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import ma.smsgateway.App
import ma.smsgateway.service.GatewayService
import java.util.concurrent.TimeUnit

/**
 * Filet de sécurité toutes les 15 minutes : relance le service s'il a été tué
 * (surcouches constructeur agressives : Xiaomi, Huawei, Oppo…).
 */
class WatchdogWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result {
        val app = App.from(applicationContext)
        if (!app.prefs.isPaired) return Result.success()
        if (!GatewayService.running) {
            app.log.warn("Service arrêté par le système : redémarrage")
            GatewayService.start(applicationContext)
        }
        return Result.success()
    }

    companion object {
        private const val NAME = "gateway-watchdog"

        fun schedule(context: Context) {
            val request = PeriodicWorkRequestBuilder<WatchdogWorker>(15, TimeUnit.MINUTES)
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.NOT_REQUIRED).build())
                .build()
            WorkManager.getInstance(context)
                .enqueueUniquePeriodicWork(NAME, ExistingPeriodicWorkPolicy.KEEP, request)
        }

        fun cancel(context: Context) = WorkManager.getInstance(context).cancelUniqueWork(NAME)
    }
}

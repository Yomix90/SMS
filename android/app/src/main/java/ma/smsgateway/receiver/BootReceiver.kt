package ma.smsgateway.receiver

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import ma.smsgateway.App
import ma.smsgateway.service.GatewayService
import ma.smsgateway.work.WatchdogWorker

/** Redémarre la passerelle après un reboot ou une mise à jour de l'APK. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val app = App.from(context)
        if (!app.prefs.isPaired) return
        app.log.info("Démarrage automatique (${intent.action?.substringAfterLast('.')})")
        WatchdogWorker.schedule(context)
        GatewayService.start(context)
    }
}

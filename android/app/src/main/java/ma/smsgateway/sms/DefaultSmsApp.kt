package ma.smsgateway.sms

import android.app.role.RoleManager
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.os.Build
import android.provider.Settings
import android.provider.Telephony

/**
 * Mode « application SMS par défaut ». Certaines ROM (vivo notamment) refusent l'envoi aux
 * applications tierces (MODEM_ERROR, resultCode 111) mais l'acceptent de l'application SMS
 * par défaut. Dans ce mode, Android n'enregistre plus lui-même les SMS dans l'historique du
 * téléphone : on les écrit ici pour que l'application Messages continue de les afficher.
 */
object DefaultSmsApp {

    // getDefaultSmsPackage renvoie null sur Android récent (visibilité des paquets) : RoleManager d'abord
    fun isDefault(context: Context): Boolean =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            context.getSystemService(RoleManager::class.java).isRoleHeld(RoleManager.ROLE_SMS)
        } else {
            Telephony.Sms.getDefaultSmsPackage(context) == context.packageName
        }

    /** Boîte de dialogue système proposant de devenir l'application SMS par défaut. */
    fun requestIntent(context: Context): Intent =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            context.getSystemService(RoleManager::class.java).createRequestRoleIntent(RoleManager.ROLE_SMS)
        } else {
            Intent(Telephony.Sms.Intents.ACTION_CHANGE_DEFAULT)
                .putExtra(Telephony.Sms.Intents.EXTRA_PACKAGE_NAME, context.packageName)
        }

    /** Écran « Applications par défaut » pour revenir à l'application Messages. */
    fun restoreIntent(): Intent = Intent(Settings.ACTION_MANAGE_DEFAULT_APPS_SETTINGS)

    fun writeInbox(context: Context, phone: String, body: String, sentAt: Long, subId: Int?) =
        write(context, Telephony.Sms.Inbox.CONTENT_URI, phone, body, subId) {
            put(Telephony.Sms.DATE_SENT, sentAt)
            put(Telephony.Sms.READ, 0)
            put(Telephony.Sms.SEEN, 0)
        }

    fun writeSent(context: Context, phone: String, body: String, subId: Int?) =
        write(context, Telephony.Sms.Sent.CONTENT_URI, phone, body, subId) {
            put(Telephony.Sms.READ, 1)
            put(Telephony.Sms.SEEN, 1)
        }

    private fun write(context: Context, uri: android.net.Uri, phone: String, body: String, subId: Int?,
                      extra: ContentValues.() -> Unit) {
        // Seule l'application par défaut peut écrire dans l'historique
        if (!isDefault(context)) return
        runCatching {
            context.contentResolver.insert(uri, ContentValues().apply {
                put(Telephony.Sms.ADDRESS, phone)
                put(Telephony.Sms.BODY, body)
                put(Telephony.Sms.DATE, System.currentTimeMillis())
                if (subId != null && subId >= 0) put(Telephony.Sms.SUBSCRIPTION_ID, subId)
                extra()
            })
        }.onFailure { android.util.Log.w("SmsGateway", "Écriture de l'historique SMS impossible", it) }
    }
}

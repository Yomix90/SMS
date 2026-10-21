package ma.smsgateway.ui

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.PowerManager
import android.provider.Settings
import androidx.core.content.ContextCompat
import ma.smsgateway.R

/** Permissions nécessaires et exclusion de l'optimisation de batterie. */
object Permissions {

    val required: Array<String> = buildList {
        add(Manifest.permission.SEND_SMS)
        add(Manifest.permission.RECEIVE_SMS)
        add(Manifest.permission.READ_SMS)
        add(Manifest.permission.READ_PHONE_STATE)
        add(Manifest.permission.READ_PHONE_NUMBERS)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) add(Manifest.permission.POST_NOTIFICATIONS)
    }.toTypedArray()

    fun missing(context: Context): List<String> = required.filter {
        ContextCompat.checkSelfPermission(context, it) != PackageManager.PERMISSION_GRANTED
    }

    fun isIgnoringBatteryOptimizations(context: Context): Boolean =
        context.getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(context.packageName)

    /** Ouvre la demande système d'exclusion (distribution APK : autorisé hors Google Play). */
    @SuppressLint("BatteryLife")
    fun batteryOptimizationIntent(context: Context): Intent =
        Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:${context.packageName}"))

    fun appSettingsIntent(context: Context): Intent =
        Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:${context.packageName}"))

    /** Conseil spécifique aux surcouches qui tuent les services (démarrage automatique). */
    fun manufacturerHint(context: Context): String? {
        val res = when (Build.MANUFACTURER.lowercase()) {
            "xiaomi", "redmi", "poco" -> R.string.hint_xiaomi
            "huawei", "honor" -> R.string.hint_huawei
            "oppo", "realme", "oneplus" -> R.string.hint_oppo
            "samsung" -> R.string.hint_samsung
            "vivo" -> R.string.hint_vivo
            else -> return null
        }
        return context.getString(res)
    }
}

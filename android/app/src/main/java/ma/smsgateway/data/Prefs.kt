package ma.smsgateway.data

import android.content.Context
import android.content.SharedPreferences
import android.util.Log
import androidx.core.content.edit
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/**
 * Préférences de l'application. Le token d'appareil est stocké chiffré (clé dans l'Android Keystore).
 * Les limites d'envoi sont fixées depuis le tableau de bord et reçues via « config.update ».
 */
class Prefs(context: Context) {

    private val prefs: SharedPreferences = try {
        val key = MasterKey.Builder(context).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build()
        EncryptedSharedPreferences.create(
            context, FILE, key,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
    } catch (e: Exception) {
        // Keystore défaillant sur certains appareils : repli non chiffré plutôt qu'un crash
        Log.e("Prefs", "EncryptedSharedPreferences indisponible", e)
        context.getSharedPreferences("$FILE.plain", Context.MODE_PRIVATE)
    }

    var serverUrl: String
        get() = prefs.getString(K_URL, "") ?: ""
        set(v) = prefs.edit { putString(K_URL, v.trimEnd('/')) }

    var token: String?
        get() = prefs.getString(K_TOKEN, null)
        set(v) = prefs.edit { putString(K_TOKEN, v) }

    var deviceId: String
        get() = prefs.getString(K_DEVICE_ID, "") ?: ""
        set(v) = prefs.edit { putString(K_DEVICE_ID, v) }

    var deviceName: String
        get() = prefs.getString(K_DEVICE_NAME, "") ?: ""
        set(v) = prefs.edit { putString(K_DEVICE_NAME, v) }

    var defaultSimSlot: Int
        get() = prefs.getInt(K_SIM, 0)
        set(v) = prefs.edit { putInt(K_SIM, v) }

    var sendIntervalSec: Int
        get() = prefs.getInt(K_INTERVAL, 5)
        set(v) = prefs.edit { putInt(K_INTERVAL, v.coerceAtLeast(1)) }

    var maxPerHour: Int
        get() = prefs.getInt(K_MAX_HOUR, 100)
        set(v) = prefs.edit { putInt(K_MAX_HOUR, v) }

    var maxPerDay: Int
        get() = prefs.getInt(K_MAX_DAY, 500)
        set(v) = prefs.edit { putInt(K_MAX_DAY, v) }

    var heartbeatSec: Int
        get() = prefs.getInt(K_HEARTBEAT, 30)
        set(v) = prefs.edit { putInt(K_HEARTBEAT, v.coerceIn(10, 300)) }

    /** Envois autorisés par le serveur (appareil actif). */
    var serverEnabled: Boolean
        get() = prefs.getBoolean(K_ENABLED, true)
        set(v) = prefs.edit { putBoolean(K_ENABLED, v) }

    /** Pause locale décidée sur le téléphone. */
    var paused: Boolean
        get() = prefs.getBoolean(K_PAUSED, false)
        set(v) = prefs.edit { putBoolean(K_PAUSED, v) }

    /** Pas d'envoi sous ce niveau de batterie, sauf en charge (0 = désactivé). */
    var minBatteryPercent: Int
        get() = prefs.getInt(K_MIN_BATTERY, 15)
        set(v) = prefs.edit { putInt(K_MIN_BATTERY, v.coerceIn(0, 90)) }

    val isPaired: Boolean get() = !token.isNullOrBlank() && serverUrl.isNotBlank()

    fun applyConfig(json: org.json.JSONObject) {
        if (json.has("device_id")) deviceId = json.optString("device_id")
        if (json.has("name")) deviceName = json.optString("name")
        if (json.has("default_sim_slot")) defaultSimSlot = json.optInt("default_sim_slot", 0)
        if (json.has("send_interval_sec")) sendIntervalSec = json.optInt("send_interval_sec", 5)
        if (json.has("max_per_hour")) maxPerHour = json.optInt("max_per_hour", 100)
        if (json.has("max_per_day")) maxPerDay = json.optInt("max_per_day", 500)
        if (json.has("heartbeat_sec")) heartbeatSec = json.optInt("heartbeat_sec", 30)
        if (json.has("enabled")) serverEnabled = json.optBoolean("enabled", true)
    }

    fun clearPairing() = prefs.edit {
        remove(K_TOKEN); remove(K_DEVICE_ID); remove(K_DEVICE_NAME)
    }

    private companion object {
        const val FILE = "gateway_prefs"
        const val K_URL = "server_url"
        const val K_TOKEN = "device_token"
        const val K_DEVICE_ID = "device_id"
        const val K_DEVICE_NAME = "device_name"
        const val K_SIM = "default_sim"
        const val K_INTERVAL = "send_interval"
        const val K_MAX_HOUR = "max_hour"
        const val K_MAX_DAY = "max_day"
        const val K_HEARTBEAT = "heartbeat"
        const val K_ENABLED = "server_enabled"
        const val K_PAUSED = "paused"
        const val K_MIN_BATTERY = "min_battery"
    }
}

package ma.smsgateway.sms

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.telephony.SubscriptionInfo
import android.telephony.SubscriptionManager
import androidx.core.content.ContextCompat
import org.json.JSONArray
import org.json.JSONObject

data class SimInfo(val slot: Int, val subscriptionId: Int, val carrier: String?, val number: String?)

/** Double SIM : correspondance emplacement physique ↔ identifiant d'abonnement Android. */
class SimManager(private val context: Context) {

    private val subs = context.getSystemService(SubscriptionManager::class.java)

    private fun hasPhoneState() =
        ContextCompat.checkSelfPermission(context, Manifest.permission.READ_PHONE_STATE) == PackageManager.PERMISSION_GRANTED

    @SuppressLint("MissingPermission")
    fun activeSims(): List<SimInfo> {
        if (!hasPhoneState() || subs == null) return emptyList()
        return try {
            (subs.activeSubscriptionInfoList ?: emptyList()).map { info ->
                SimInfo(info.simSlotIndex, info.subscriptionId, info.carrierName?.toString(), numberOf(info))
            }.sortedBy { it.slot }
        } catch (e: SecurityException) {
            emptyList()
        }
    }

    /** Identifiant d'abonnement pour un emplacement, ou null (SIM absente / permission manquante). */
    fun subscriptionIdForSlot(slot: Int): Int? = activeSims().firstOrNull { it.slot == slot }?.subscriptionId

    /** Emplacement à partir d'un identifiant d'abonnement (SMS entrants). */
    fun slotForSubscription(subscriptionId: Int): Int? =
        activeSims().firstOrNull { it.subscriptionId == subscriptionId }?.slot

    @SuppressLint("MissingPermission", "HardwareIds")
    private fun numberOf(info: SubscriptionInfo): String? = try {
        val number = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            val granted = ContextCompat.checkSelfPermission(context, Manifest.permission.READ_PHONE_NUMBERS) ==
                PackageManager.PERMISSION_GRANTED
            if (granted) subs?.getPhoneNumber(info.subscriptionId) else null
        } else {
            @Suppress("DEPRECATION") info.number
        }
        number?.takeIf { it.isNotBlank() }
    } catch (e: Exception) {
        null
    }

    fun toJson(): JSONArray = JSONArray().apply {
        activeSims().forEach { sim ->
            put(JSONObject().put("slot", sim.slot).put("subscription_id", sim.subscriptionId)
                .put("carrier", sim.carrier ?: JSONObject.NULL).put("phone_number", sim.number ?: JSONObject.NULL))
        }
    }
}

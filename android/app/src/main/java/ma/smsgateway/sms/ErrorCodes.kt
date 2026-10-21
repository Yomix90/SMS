package ma.smsgateway.sms

import android.app.Activity
import android.telephony.SmsManager

/** Codes d'erreur remontés au serveur (le serveur relance automatiquement les erreurs temporaires). */
object ErrorCodes {
    const val GENERIC_FAILURE = "GENERIC_FAILURE"
    const val RADIO_OFF = "RADIO_OFF"
    const val NULL_PDU = "NULL_PDU"
    const val NO_SERVICE = "NO_SERVICE"
    const val LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    const val SHORT_CODE_NOT_ALLOWED = "SHORT_CODE_NOT_ALLOWED"
    const val NETWORK_ERROR = "NETWORK_ERROR"
    const val MODEM_ERROR = "MODEM_ERROR"
    const val INVALID_NUMBER = "INVALID_NUMBER"
    const val PERMISSION_DENIED = "PERMISSION_DENIED"
    const val NO_SIM = "NO_SIM"
    const val SIM_NOT_READY = "SIM_NOT_READY"
    const val DELIVERY_FAILED = "DELIVERY_FAILED"
    const val TIMEOUT = "TIMEOUT"

    /** Traduit le resultCode de l'accusé d'envoi. null = succès. */
    fun fromSentResult(resultCode: Int): String? = when (resultCode) {
        Activity.RESULT_OK -> null
        SmsManager.RESULT_ERROR_GENERIC_FAILURE -> GENERIC_FAILURE
        SmsManager.RESULT_ERROR_RADIO_OFF -> RADIO_OFF
        SmsManager.RESULT_ERROR_NULL_PDU -> NULL_PDU
        SmsManager.RESULT_ERROR_NO_SERVICE -> NO_SERVICE
        SmsManager.RESULT_ERROR_LIMIT_EXCEEDED -> LIMIT_EXCEEDED
        SmsManager.RESULT_ERROR_SHORT_CODE_NOT_ALLOWED,
        SmsManager.RESULT_ERROR_SHORT_CODE_NEVER_ALLOWED -> SHORT_CODE_NOT_ALLOWED
        SmsManager.RESULT_NETWORK_ERROR, SmsManager.RESULT_NETWORK_REJECT -> NETWORK_ERROR
        SmsManager.RESULT_MODEM_ERROR, SmsManager.RESULT_RIL_MODEM_ERR -> MODEM_ERROR
        SmsManager.RESULT_INVALID_SMS_FORMAT, SmsManager.RESULT_INVALID_ARGUMENTS,
        SmsManager.RESULT_INVALID_SMSC_ADDRESS -> INVALID_NUMBER
        SmsManager.RESULT_NO_DEFAULT_SMS_APP -> PERMISSION_DENIED
        SmsManager.RESULT_RIL_SIM_ABSENT -> NO_SIM
        SmsManager.RESULT_RIL_RADIO_NOT_AVAILABLE -> RADIO_OFF
        else -> "${GENERIC_FAILURE}_$resultCode"
    }
}

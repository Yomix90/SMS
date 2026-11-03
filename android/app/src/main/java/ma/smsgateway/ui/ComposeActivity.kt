package ma.smsgateway.ui

import android.content.Intent
import android.os.Bundle
import android.text.InputType
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import ma.smsgateway.R
import ma.smsgateway.sms.DefaultSmsApp
import android.telephony.SmsManager

/**
 * Rédaction minimale d'un SMS (liens « smsto: », bouton « Message » des contacts).
 * Exigée par Android pour l'application SMS par défaut ; l'usage normal reste l'application Messages.
 */
class ComposeActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val phoneInput = EditText(this).apply {
            hint = getString(R.string.compose_to)
            inputType = InputType.TYPE_CLASS_PHONE
            setText(intent.data?.schemeSpecificPart?.substringBefore('?').orEmpty())
        }
        val bodyInput = EditText(this).apply {
            hint = getString(R.string.compose_body)
            minLines = 3
            setText(intent.getStringExtra("sms_body") ?: intent.getStringExtra(Intent.EXTRA_TEXT).orEmpty())
        }
        val form = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(56, 24, 56, 0)
            addView(phoneInput)
            addView(bodyInput)
        }
        MaterialAlertDialogBuilder(this)
            .setTitle(R.string.compose_title)
            .setView(form)
            .setPositiveButton(R.string.compose_send) { _, _ ->
                send(phoneInput.text.toString().trim(), bodyInput.text.toString())
            }
            .setNegativeButton(android.R.string.cancel, null)
            .setOnDismissListener { finish() }
            .show()
    }

    private fun send(phone: String, body: String) {
        if (phone.isBlank() || body.isBlank()) return
        val ok = runCatching {
            @Suppress("DEPRECATION")
            val manager = SmsManager.getDefault()
            manager.sendMultipartTextMessage(phone, null, manager.divideMessage(body), null, null)
            DefaultSmsApp.writeSent(this, phone, body, null)
        }.isSuccess
        Toast.makeText(this, if (ok) R.string.compose_sent else R.string.compose_failed, Toast.LENGTH_SHORT).show()
    }
}

package ma.smsgateway.ui

import android.content.Intent
import android.os.Bundle
import android.provider.Settings
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.isVisible
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import ma.smsgateway.App
import ma.smsgateway.BuildConfig
import ma.smsgateway.R
import ma.smsgateway.databinding.ActivitySettingsBinding
import ma.smsgateway.sms.DefaultSmsApp
import ma.smsgateway.sms.SimManager
import androidx.activity.result.contract.ActivityResultContracts
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch

/**
 * Paramètres : serveur, SIM, limites d'envoi (définies depuis le tableau de bord),
 * seuil de batterie local, optimisation de batterie, désappairage.
 */
class SettingsActivity : AppCompatActivity() {

    private lateinit var binding: ActivitySettingsBinding
    private val prefs by lazy { App.from(this).prefs }

    private val defaultSmsRequest = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) {
        val log = App.from(this).log
        if (DefaultSmsApp.isDefault(this)) log.info("SMS Gateway est l'application SMS par défaut")
        else log.info("SMS Gateway n'est pas l'application SMS par défaut")
        render()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivitySettingsBinding.inflate(layoutInflater)
        setContentView(binding.root)
        binding.root.applySystemBarInsets()
        setSupportActionBar(binding.toolbar)
        supportActionBar?.setDisplayHomeAsUpEnabled(true)

        binding.minBattery.value = prefs.minBatteryPercent.toFloat().coerceIn(0f, 90f)
        binding.minBatteryLabel.text = batteryLabel(prefs.minBatteryPercent)
        binding.minBattery.addOnChangeListener { _, value, _ ->
            prefs.minBatteryPercent = value.toInt()
            binding.minBatteryLabel.text = batteryLabel(value.toInt())
        }

        binding.deliveryReports.isChecked = prefs.deliveryReports
        binding.deliveryReports.setOnCheckedChangeListener { _, checked ->
            prefs.deliveryReports = checked
            App.from(this).log.info(if (checked) "Accusés de livraison activés" else "Accusés de livraison désactivés")
        }

        binding.btnDiagnostic.setOnClickListener { askDiagnosticNumber() }
        binding.btnDefaultSms.setOnClickListener {
            val intent = if (DefaultSmsApp.isDefault(this)) DefaultSmsApp.restoreIntent() else DefaultSmsApp.requestIntent(this)
            runCatching { defaultSmsRequest.launch(intent) }
                .onFailure { startActivity(Permissions.appSettingsIntent(this)) }
        }

        binding.btnBattery.setOnClickListener {
            runCatching { startActivity(Permissions.batteryOptimizationIntent(this)) }
                .onFailure { startActivity(Permissions.appSettingsIntent(this)) }
        }
        binding.btnAppSettings.setOnClickListener { startActivity(Permissions.appSettingsIntent(this)) }
        binding.btnNotifications.setOnClickListener {
            startActivity(Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                .putExtra(Settings.EXTRA_APP_PACKAGE, packageName))
        }
        binding.btnUnpair.setOnClickListener { confirmUnpair() }
        binding.version.text = getString(R.string.version_label, BuildConfig.VERSION_NAME)
    }

    override fun onResume() {
        super.onResume()
        render()
    }

    private fun render() {
        val paired = prefs.isPaired
        binding.serverUrl.text = prefs.serverUrl.ifBlank { "—" }
        binding.deviceName.text = prefs.deviceName.ifBlank { "—" }
        binding.limits.text = getString(R.string.limits_value, prefs.sendIntervalSec, prefs.maxPerHour, prefs.maxPerDay)
        binding.serverEnabled.text = getString(if (prefs.serverEnabled) R.string.enabled_yes else R.string.enabled_no)

        val sims = SimManager(this).activeSims()
        binding.sims.text = if (sims.isEmpty()) getString(R.string.no_sim_info) else sims.joinToString("\n") { sim ->
            val default = if (sim.slot == prefs.defaultSimSlot) " ★" else ""
            "SIM ${sim.slot + 1} · ${sim.carrier ?: "?"}${sim.number?.let { " · $it" } ?: ""}$default"
        }
        binding.batteryStatus.text = getString(
            if (Permissions.isIgnoringBatteryOptimizations(this)) R.string.battery_unrestricted else R.string.battery_restricted)
        binding.oemHint.text = Permissions.manufacturerHint(this)
        binding.oemHint.isVisible = binding.oemHint.text.isNotBlank()
        binding.btnUnpair.isEnabled = paired
        binding.sendVariant.text = getString(R.string.send_variant, prefs.sendOptions().describe())
        val isDefault = DefaultSmsApp.isDefault(this)
        binding.defaultSmsStatus.text = getString(if (isDefault) R.string.default_sms_yes else R.string.default_sms_no)
        binding.btnDefaultSms.text = getString(if (isDefault) R.string.default_sms_restore else R.string.default_sms_set)
    }

    // --- Diagnostic d'envoi -------------------------------------------------------------

    private fun askDiagnosticNumber() {
        val input = android.widget.EditText(this).apply {
            hint = "06 12 34 56 78"
            inputType = android.text.InputType.TYPE_CLASS_PHONE
            setPadding(48, 32, 48, 32)
        }
        MaterialAlertDialogBuilder(this)
            .setTitle(R.string.diagnostic_button)
            .setMessage(R.string.diagnostic_number)
            .setView(input)
            .setPositiveButton(R.string.diagnostic_next) { _, _ ->
                val phone = ma.smsgateway.sms.SendDiagnostic.normalize(input.text.toString())
                if (phone.length >= 8) askDiagnosticSim(phone)
            }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }

    private fun askDiagnosticSim(phone: String) {
        val sims = SimManager(this).activeSims()
        val labels = if (sims.isEmpty()) arrayOf("SIM 1") else
            sims.map { "SIM ${it.slot + 1} · ${it.carrier ?: "?"}" }.toTypedArray()
        MaterialAlertDialogBuilder(this)
            .setTitle(R.string.diagnostic_sim)
            .setItems(labels) { _, which -> runDiagnostic(phone, sims.getOrNull(which)?.slot ?: 0) }
            .show()
    }

    private fun runDiagnostic(phone: String, slot: Int) {
        val text = android.widget.TextView(this).apply {
            setPadding(56, 24, 56, 0)
            textSize = 13f
            text = getString(R.string.diagnostic_running, phone, slot + 1)
        }
        val dialog = MaterialAlertDialogBuilder(this)
            .setTitle(R.string.diagnostic_button)
            .setView(text)
            .setCancelable(false)
            .setPositiveButton(android.R.string.ok, null)
            .show()
        dialog.getButton(android.content.DialogInterface.BUTTON_POSITIVE).isEnabled = false
        lifecycleScope.launch {
            val found = ma.smsgateway.sms.SendDiagnostic(this@SettingsActivity).run(phone, slot) { step ->
                runOnUiThread {
                    text.append("\n${if (step.ok) "✓" else "✗"} ${step.index}/${step.total} · ${step.options.describe()}" +
                        if (step.ok) "" else "\n    ${step.detail}")
                }
            }
            text.append("\n\n" + if (found != null) getString(R.string.diagnostic_found, found.describe())
                else getString(R.string.diagnostic_none))
            dialog.getButton(android.content.DialogInterface.BUTTON_POSITIVE).isEnabled = true
            render()
        }
    }

    private fun batteryLabel(value: Int) =
        if (value == 0) getString(R.string.min_battery_off) else getString(R.string.min_battery_value, value)

    private fun confirmUnpair() {
        MaterialAlertDialogBuilder(this)
            .setTitle(R.string.unpair)
            .setMessage(R.string.unpair_confirm)
            .setPositiveButton(R.string.unpair) { _, _ ->
                Pairing.unpair(this)
                render()
            }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }

    override fun onSupportNavigateUp(): Boolean {
        finish()
        return true
    }
}

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
import ma.smsgateway.sms.SimManager

/**
 * Paramètres : serveur, SIM, limites d'envoi (définies depuis le tableau de bord),
 * seuil de batterie local, optimisation de batterie, désappairage.
 */
class SettingsActivity : AppCompatActivity() {

    private lateinit var binding: ActivitySettingsBinding
    private val prefs by lazy { App.from(this).prefs }

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

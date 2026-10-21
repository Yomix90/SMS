package ma.smsgateway.ui

import android.content.Intent
import android.os.Bundle
import android.text.format.DateUtils
import android.view.Menu
import android.view.MenuItem
import android.view.View
import android.widget.EditText
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.isVisible
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import androidx.recyclerview.widget.LinearLayoutManager
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.google.android.material.snackbar.Snackbar
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import kotlinx.coroutines.launch
import ma.smsgateway.App
import ma.smsgateway.R
import ma.smsgateway.databinding.ActivityMainBinding
import ma.smsgateway.service.GatewayService
import ma.smsgateway.service.GatewayState
import java.util.Calendar

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private val app by lazy { App.from(this) }
    private val logAdapter = LogAdapter()

    private val permissionLauncher = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        refreshWarnings()
        if (app.prefs.isPaired && Permissions.missing(this).isEmpty()) GatewayService.start(this)
    }

    private val scanLauncher = registerForActivityResult(ScanContract()) { result ->
        result.contents?.let { pair(it) }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)
        binding.root.applySystemBarInsets()
        setSupportActionBar(binding.toolbar)

        binding.logList.layoutManager = LinearLayoutManager(this)
        binding.logList.adapter = logAdapter

        binding.btnScan.setOnClickListener { startScan() }
        binding.btnManual.setOnClickListener { manualPairing() }
        binding.btnPermissions.setOnClickListener { permissionLauncher.launch(Permissions.required) }
        binding.btnBattery.setOnClickListener {
            runCatching { startActivity(Permissions.batteryOptimizationIntent(this)) }
                .onFailure { startActivity(Permissions.appSettingsIntent(this)) }
        }
        binding.btnPause.setOnClickListener { togglePause() }
        binding.btnReconnect.setOnClickListener {
            GatewayService.start(this, GatewayService.ACTION_RECONNECT)
            Snackbar.make(binding.root, R.string.reconnecting, Snackbar.LENGTH_SHORT).show()
        }

        observe()
        if (Permissions.missing(this).isNotEmpty()) permissionLauncher.launch(Permissions.required)
    }

    override fun onResume() {
        super.onResume()
        refreshWarnings()
        refreshPairing()
        if (app.prefs.isPaired && !GatewayService.running && Permissions.missing(this).isEmpty()) {
            GatewayService.start(this)
        }
    }

    private fun observe() {
        val dayStart = Calendar.getInstance().apply {
            set(Calendar.HOUR_OF_DAY, 0); set(Calendar.MINUTE, 0); set(Calendar.SECOND, 0); set(Calendar.MILLISECOND, 0)
        }.timeInMillis
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                launch { GatewayState.state.collect(::renderState) }
                launch { app.db.logs().latest().collect { logAdapter.submitList(it) } }
                launch { app.db.outgoing().sentSinceFlow(dayStart).collect { binding.statSent.text = it.toString() } }
                launch { app.db.incoming().receivedSinceFlow(dayStart).collect { binding.statReceived.text = it.toString() } }
                launch { app.db.outgoing().queueSizeFlow().collect { binding.statQueue.text = it.toString() } }
            }
        }
    }

    private fun renderState(s: GatewayState.Snapshot) {
        val paired = app.prefs.isPaired
        val (label, color) = when {
            !paired -> getString(R.string.status_unpaired) to R.color.muted
            !s.serviceRunning -> getString(R.string.status_stopped) to R.color.error
            s.link == GatewayState.Link.WEBSOCKET -> getString(R.string.status_online) to R.color.ok
            s.link == GatewayState.Link.POLLING -> getString(R.string.status_polling) to R.color.warn
            s.link == GatewayState.Link.CONNECTING -> getString(R.string.status_connecting) to R.color.warn
            else -> getString(R.string.status_offline) to R.color.error
        }
        binding.statusText.text = label
        binding.statusDot.setColorFilter(ContextCompat.getColor(this, color))
        binding.statusDetail.text = buildString {
            if (paired) {
                append(app.prefs.deviceName.ifBlank { getString(R.string.app_name) })
                append(" · ").append(app.prefs.serverUrl.removePrefix("https://").removePrefix("http://"))
            }
            s.lastHeartbeat?.let {
                append("\n").append(getString(R.string.last_contact,
                    DateUtils.getRelativeTimeSpanString(it, System.currentTimeMillis(), DateUtils.SECOND_IN_MILLIS)))
            }
        }
        binding.blockedText.isVisible = s.blockedReason != null
        binding.blockedText.text = s.blockedReason
        binding.errorText.isVisible = s.lastError != null && s.link != GatewayState.Link.WEBSOCKET
        binding.errorText.text = s.lastError
        binding.btnPause.setText(if (app.prefs.paused) R.string.resume else R.string.pause)
        binding.actionsRow.isVisible = paired
        refreshPairing()
    }

    private fun refreshPairing() {
        val paired = app.prefs.isPaired
        binding.pairCard.isVisible = !paired
        binding.httpWarning.isVisible = paired && app.prefs.serverUrl.startsWith("http://")
    }

    private fun refreshWarnings() {
        binding.permissionsCard.isVisible = Permissions.missing(this).isNotEmpty()
        val batteryOk = Permissions.isIgnoringBatteryOptimizations(this)
        binding.batteryCard.isVisible = !batteryOk
        binding.batteryHint.text = listOfNotNull(getString(R.string.battery_warning), Permissions.manufacturerHint(this))
            .joinToString("\n\n")
    }

    private fun startScan() {
        scanLauncher.launch(ScanOptions().apply {
            setDesiredBarcodeFormats(ScanOptions.QR_CODE)
            setPrompt(getString(R.string.scan_prompt))
            setBeepEnabled(false)
            setOrientationLocked(false)
        })
    }

    private fun manualPairing() {
        val input = EditText(this).apply {
            hint = getString(R.string.manual_hint)
            minLines = 3
            setPadding(48, 32, 48, 32)
        }
        MaterialAlertDialogBuilder(this)
            .setTitle(R.string.manual_pairing)
            .setView(input)
            .setPositiveButton(R.string.pair) { _, _ -> pair(input.text.toString()) }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }

    private fun pair(raw: String) {
        binding.progress.visibility = View.VISIBLE
        lifecycleScope.launch {
            try {
                val name = Pairing.pair(this@MainActivity, raw)
                Snackbar.make(binding.root, getString(R.string.paired_ok, name), Snackbar.LENGTH_LONG).show()
                if (!Permissions.isIgnoringBatteryOptimizations(this@MainActivity)) refreshWarnings()
            } catch (e: Pairing.PairingException) {
                MaterialAlertDialogBuilder(this@MainActivity)
                    .setTitle(R.string.pairing_failed)
                    .setMessage(e.localized(this@MainActivity))
                    .setPositiveButton(android.R.string.ok, null)
                    .show()
            } finally {
                binding.progress.visibility = View.GONE
                refreshPairing()
            }
        }
    }

    private fun togglePause() {
        app.prefs.paused = !app.prefs.paused
        app.log.info(if (app.prefs.paused) getString(R.string.paused_log) else getString(R.string.resumed_log))
        GatewayState.set { it.copy(blockedReason = if (app.prefs.paused) getString(R.string.blocked_paused) else null) }
        GatewayService.requestFlush()
        GatewayService.start(this)
    }

    override fun onCreateOptionsMenu(menu: Menu): Boolean {
        menuInflater.inflate(R.menu.main, menu)
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean = when (item.itemId) {
        R.id.action_settings -> {
            startActivity(Intent(this, SettingsActivity::class.java)); true
        }
        else -> super.onOptionsItemSelected(item)
    }
}

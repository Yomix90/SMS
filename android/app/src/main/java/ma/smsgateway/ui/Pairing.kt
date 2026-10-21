package ma.smsgateway.ui

import android.content.Context
import android.os.Build
import ma.smsgateway.App
import ma.smsgateway.BuildConfig
import ma.smsgateway.R
import ma.smsgateway.net.ApiException
import ma.smsgateway.service.GatewayService
import ma.smsgateway.sms.SimManager
import ma.smsgateway.work.WatchdogWorker
import org.json.JSONObject

/** Appairage à partir du contenu du QR code : {"v":1,"url":"https://…","code":"…"}. */
object Pairing {

    /** Erreur d'appairage traduisible (ressource + détail technique éventuel). */
    class PairingException(val resId: Int, val detail: String? = null) : Exception(detail) {
        fun localized(context: Context): String =
            context.getString(resId) + (detail?.let { "\n\n$it" } ?: "")
    }

    private val URL_RE = Regex("""https?://[^\s,"'{}]+""")
    private val CODE_RE = Regex("""code["']?\s*[:=]\s*["']?([A-Za-z0-9_-]{16,})""")
    private val TOKEN_RE = Regex("""[A-Za-z0-9_-]{16,}""")

    /**
     * Accepte le JSON du QR, mais aussi une saisie manuelle simplifiée :
     * JSON sans guillemets, ou « https://serveur code ».
     */
    fun parse(raw: String): Pair<String, String> {
        val text = raw.trim()
        val json = runCatching { JSONObject(text) }.getOrNull()
        var url = json?.optString("url").orEmpty()
        var code = json?.optString("code").orEmpty()
        if (json == null) {
            url = URL_RE.find(text)?.value.orEmpty()
            val rest = if (url.isNotEmpty()) text.replace(url, " ") else text
            code = CODE_RE.find(rest)?.groupValues?.get(1) ?: TOKEN_RE.findAll(rest).lastOrNull()?.value.orEmpty()
            if (url.isEmpty() && code.isEmpty()) throw PairingException(R.string.err_qr_unknown)
        }
        url = url.trimEnd('/')
        if (!(url.startsWith("http://") || url.startsWith("https://")) || code.isBlank()) {
            throw PairingException(R.string.err_qr_incomplete)
        }
        return url to code
    }

    suspend fun pair(context: Context, raw: String): String {
        val app = App.from(context)
        val (url, code) = parse(raw)
        val info = JSONObject()
            .put("model", Build.MODEL)
            .put("manufacturer", Build.MANUFACTURER)
            .put("android_version", Build.VERSION.RELEASE)
            .put("app_version", BuildConfig.VERSION_NAME)
            .put("name", "${Build.MANUFACTURER} ${Build.MODEL}")
            .put("sims", SimManager(context).toJson())
        val res = try {
            app.api.pair(url, code, info)
        } catch (e: ApiException) {
            throw if (e.httpCode == 400) PairingException(R.string.err_code_expired)
            else PairingException(R.string.err_server, e.message)
        } catch (e: java.io.IOException) {
            throw PairingException(R.string.err_unreachable, e.message)
        }
        app.prefs.serverUrl = url
        app.prefs.token = res.getString("token")
        res.optJSONObject("device")?.let { app.prefs.applyConfig(it) }
        app.prefs.paused = false
        app.log.ok("Appairé avec $url (${app.prefs.deviceName})")
        WatchdogWorker.schedule(context)
        GatewayService.start(context)
        return app.prefs.deviceName
    }

    fun unpair(context: Context) {
        val app = App.from(context)
        GatewayService.stop(context)
        WatchdogWorker.cancel(context)
        app.prefs.clearPairing()
        app.log.warn("Appareil désappairé")
    }
}

package ma.smsgateway.net

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import ma.smsgateway.data.Prefs
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

/** Erreur HTTP du serveur (code + code métier de l'enveloppe {"error": {...}}). */
class ApiException(val httpCode: Int, val errorCode: String, message: String) : IOException(message) {
    val isUnauthorized get() = httpCode == 401
}

/**
 * API HTTP « /device/v1 » : appairage et repli en polling quand le WebSocket est indisponible.
 * Toutes les méthodes sont suspendues et s'exécutent sur Dispatchers.IO.
 */
class ApiClient(private val prefs: Prefs) {

    val http: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .retryOnConnectionFailure(true)
        .build()

    private val json = "application/json; charset=utf-8".toMediaType()

    /** Appairage : échange le code du QR contre un token d'appareil. */
    suspend fun pair(serverUrl: String, code: String, info: JSONObject): JSONObject {
        val body = JSONObject(info.toString()).put("code", code)
        return call("POST", serverUrl.trimEnd('/') + "/device/v1/pair", body, auth = false)
    }

    suspend fun config(): JSONObject = call("GET", "/device/v1/config")

    suspend fun heartbeat(payload: JSONObject): JSONObject = call("POST", "/device/v1/heartbeat", payload)

    suspend fun tasks(limit: Int = 50): JSONArray =
        call("GET", "/device/v1/tasks?limit=$limit").optJSONArray("messages") ?: JSONArray()

    suspend fun ack(ids: List<String>): Int =
        call("POST", "/device/v1/tasks/ack", JSONObject().put("ids", JSONArray(ids))).optInt("acked")

    suspend fun statuses(reports: JSONArray): JSONObject =
        call("POST", "/device/v1/status", JSONObject().put("statuses", reports))

    suspend fun inbound(messages: JSONArray): List<String> {
        val res = call("POST", "/device/v1/inbound", JSONObject().put("messages", messages))
        val arr = res.optJSONArray("received") ?: return emptyList()
        return (0 until arr.length()).map { arr.optString(it) }
    }

    private suspend fun call(method: String, path: String, body: JSONObject? = null, auth: Boolean = true): JSONObject =
        withContext(Dispatchers.IO) {
            val url = if (path.startsWith("http")) path else prefs.serverUrl + path
            val builder = Request.Builder().url(url).header("Accept", "application/json")
            if (auth) builder.header("Authorization", "Bearer ${prefs.token ?: ""}")
            when (method) {
                "GET" -> builder.get()
                else -> builder.method(method, (body ?: JSONObject()).toString().toRequestBody(json))
            }
            http.newCall(builder.build()).execute().use { resp ->
                val text = resp.body?.string().orEmpty()
                val parsed = runCatching { JSONObject(text) }.getOrDefault(JSONObject())
                if (!resp.isSuccessful) {
                    val err = parsed.optJSONObject("error")
                    throw ApiException(resp.code, err?.optString("code") ?: "http_${resp.code}",
                        err?.optString("message") ?: "HTTP ${resp.code}")
                }
                parsed
            }
        }
}

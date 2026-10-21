package ma.smsgateway.data

import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

/** Journal applicatif : logcat + table « log » affichée à l'écran. */
class GatewayLog(private val dao: LogDao) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var writes = 0

    fun info(text: String) = write("info", text)
    fun ok(text: String) = write("ok", text)
    fun warn(text: String) = write("warn", text)
    fun error(text: String, e: Throwable? = null) {
        Log.e(TAG, text, e)
        write("error", if (e != null) "$text : ${e.message}" else text)
    }
    fun incoming(text: String) = write("in", text)
    fun outgoing(text: String) = write("out", text)

    private fun write(level: String, text: String) {
        if (level != "error") Log.i(TAG, "[$level] $text")
        scope.launch {
            dao.insert(LogEntry(level = level, text = text.take(500)))
            if (++writes % 50 == 0) dao.trim()
        }
    }

    private companion object { const val TAG = "SmsGateway" }
}

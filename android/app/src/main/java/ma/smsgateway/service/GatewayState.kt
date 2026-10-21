package ma.smsgateway.service

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update

/** État partagé entre le service et l'interface (observé par MainActivity). */
object GatewayState {

    enum class Link { OFFLINE, CONNECTING, WEBSOCKET, POLLING }

    data class Snapshot(
        val serviceRunning: Boolean = false,
        val link: Link = Link.OFFLINE,
        val lastHeartbeat: Long? = null,
        val lastError: String? = null,
        val blockedReason: String? = null,   // pourquoi l'envoi est suspendu (limite, batterie, pause…)
    )

    private val _state = MutableStateFlow(Snapshot())
    val state: StateFlow<Snapshot> = _state

    fun set(block: (Snapshot) -> Snapshot) = _state.update(block)
}

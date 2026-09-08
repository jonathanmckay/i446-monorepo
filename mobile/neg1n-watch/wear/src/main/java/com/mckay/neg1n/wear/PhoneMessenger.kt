package com.mckay.neg1n.wear

import android.content.Context
import android.util.Log
import com.google.android.gms.wearable.Wearable
import kotlinx.coroutines.tasks.await

private const val TAG = "Neg1n"

/** Sends a fire-and-forget message to the paired phone over Bluetooth —
 * works regardless of WiFi/location on either device, since it never
 * touches the internet itself (see RitualCompleter and RitualListActivity's
 * "sync now on open", the two callers). The phone does the actual network
 * call (it has Tailscale, so it can reach the backend from anywhere, unlike
 * a bare LAN IP the watch alone could never reach off the home network —
 * see RitualCompleter's doc comment for the fuller history of why this
 * isn't a direct watch HTTP call anymore).
 *
 * No request/reply channel: the caller doesn't learn success/failure from
 * this call directly, only "delivered to a connected phone or not". Callers
 * reconcile by re-pulling DataLayerReader.readLatest() after a short delay
 * instead — simpler than a full ack protocol, and self-correcting either
 * way (if the phone-side action actually failed, the re-pull just shows the
 * old state, same as if nothing had been swiped). */
object PhoneMessenger {
    suspend fun send(context: Context, path: String, payload: String = ""): Boolean {
        return try {
            val nodes = Wearable.getNodeClient(context).connectedNodes.await()
            val node = nodes.firstOrNull()
            if (node == null) {
                Log.e(TAG, "PhoneMessenger: no connected phone node for $path")
                return false
            }
            Wearable.getMessageClient(context).sendMessage(node.id, path, payload.toByteArray()).await()
            Log.i(TAG, "PhoneMessenger: sent $path to ${node.displayName}")
            true
        } catch (e: Exception) {
            Log.e(TAG, "PhoneMessenger: send $path failed", e)
            false
        }
    }
}

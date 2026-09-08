package com.mckay.neg1n.wear

import android.content.Context
import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit

private const val TAG = "Neg1n"

/** Completes a ritual by calling tools/watch/neg1n_status.py's
 * /api/neg1n/complete DIRECTLY from the watch, over its own WiFi — not
 * relayed through the phone.
 *
 * This is a deliberate asymmetry from the status READ path (which pulls via
 * the phone + Data Layer, see DataLayerReader's doc comment): a status read
 * is a passive background sync where phone-relay's reliability matters more
 * than latency, but a swipe-to-complete is a direct user action that should
 * feel instant, and by the time this was built we'd already confirmed (via
 * `adb devices -l` showing this Pixel Watch's own WiFi radio, on the same
 * home network as the backend) that a direct call works — and it sidesteps
 * relaying the action through the exact same GMS listener-service
 * verification quirk that made the phone-relay status push unreliable in
 * the other direction. The endpoint is learned from whatever the phone last
 * synced (Neg1nConfig.KEY_ENDPOINT in the status DataItem, see
 * DataLayerReader) — there's no separate config UI on the watch. */
object RitualCompleter {

    private val client = OkHttpClient.Builder()
        .connectTimeout(8, TimeUnit.SECONDS)
        .readTimeout(8, TimeUnit.SECONDS)
        .build()

    suspend fun complete(context: Context, tag: String): Result<Neg1nStatus> = withContext(Dispatchers.IO) {
        val endpoint = StatusStore.load(context).endpoint
        if (endpoint.isNullOrBlank()) {
            return@withContext Result.failure(IllegalStateException(
                "no endpoint known yet — open the phone app once to sync"))
        }
        try {
            val completeUrl = endpoint.trimEnd('/') + "/complete"
            val payload = JSONObject().put("tag", tag).toString()
                .toRequestBody("application/json".toMediaType())
            val request = Request.Builder().url(completeUrl).post(payload).build()
            client.newCall(request).execute().use { resp ->
                val text = resp.body?.string() ?: "{}"
                val json = JSONObject(text)
                when {
                    !resp.isSuccessful -> Result.failure(IllegalStateException(
                        json.optString("error", "HTTP ${resp.code}")))
                    !json.optBoolean("ok", false) -> Result.failure(IllegalStateException(
                        json.optString("stderr_tail", "did-fast.py --ritual $tag failed")))
                    else -> {
                        val statusJson = json.getJSONObject("status")
                        val block = if (statusJson.isNull("block") || !statusJson.has("block")) null
                                    else statusJson.getString("block")
                        val done = statusJson.optJSONArray("done")?.let { arr ->
                            (0 until arr.length()).map { arr.getString(it) }
                        } ?: emptyList()
                        val notDone = statusJson.optJSONArray("not_done")?.let { arr ->
                            (0 until arr.length()).map { arr.getString(it) }
                        } ?: emptyList()
                        val updatedAt = System.currentTimeMillis()
                        // Save with a fresh timestamp so DataLayerReader's
                        // newest-wins comparison keeps this until the phone's
                        // own next sync catches up (see its doc comment).
                        StatusStore.save(context, block, done, notDone, updatedAt, endpoint)
                        Result.success(Neg1nStatus(block, done.toSet(), notDone, updatedAt, endpoint))
                    }
                }
            }
        } catch (e: Exception) {
            Log.e(TAG, "RitualCompleter: complete failed for tag=$tag", e)
            Result.failure(e)
        }
    }
}

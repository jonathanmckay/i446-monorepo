package com.mckay.neg1n.phone

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/** Result of a fetch against tools/watch/neg1n_status.py's /api/neg1n. */
data class Neg1nStatus(
    val block: String?,      // null = outside the 04:00-22:00 block window
    val done: List<String>,
    val notDone: List<String>,
)

class StatusFetcher(private val baseUrl: String) {

    private val client = OkHttpClient.Builder()
        .connectTimeout(8, TimeUnit.SECONDS)
        .readTimeout(8, TimeUnit.SECONDS)
        .build()

    /** Blocking fetch — call from a background thread/WorkManager, never the UI thread. */
    fun fetch(): Result<Neg1nStatus> = try {
        val request = Request.Builder().url(baseUrl).get().build()
        client.newCall(request).execute().use { resp ->
            if (!resp.isSuccessful) {
                Result.failure(IllegalStateException("HTTP ${resp.code}"))
            } else {
                val body = resp.body?.string() ?: "{}"
                Result.success(parse(body))
            }
        }
    } catch (e: Exception) {
        Result.failure(e)
    }

    /** Completes one ritual (POST .../api/neg1n/complete) — the real
     * did-fast.py --ritual close, same as desktop dtd/inbound. Returns the
     * server's freshly-recomputed status so the caller can push it straight
     * back to the watch without a second fetch. Blocking — background
     * thread/Worker only, same as fetch(). */
    fun complete(tag: String): Result<Neg1nStatus> = try {
        val completeUrl = baseUrl.trimEnd('/') + "/complete"
        val payload = JSONObject().put("tag", tag).toString()
            .toRequestBody("application/json".toMediaType())
        val request = Request.Builder().url(completeUrl).post(payload).build()
        client.newCall(request).execute().use { resp ->
            val text = resp.body?.string() ?: "{}"
            val json = JSONObject(text)
            when {
                !resp.isSuccessful -> Result.failure(IllegalStateException(json.optString("error", "HTTP ${resp.code}")))
                !json.optBoolean("ok", false) -> Result.failure(IllegalStateException(
                    json.optString("stderr_tail", "did-fast.py --ritual $tag failed")))
                else -> Result.success(parse(json.getJSONObject("status").toString()))
            }
        }
    } catch (e: Exception) {
        Result.failure(e)
    }

    private fun parse(body: String): Neg1nStatus {
        val json = JSONObject(body)
        if (json.has("error")) throw IllegalStateException(json.getString("error"))
        val block = if (json.isNull("block") || !json.has("block")) null else json.getString("block")
        val done = json.optJSONArray("done")?.let { arr ->
            (0 until arr.length()).map { arr.getString(it) }
        } ?: emptyList()
        val notDone = json.optJSONArray("not_done")?.let { arr ->
            (0 until arr.length()).map { arr.getString(it) }
        } ?: emptyList()
        return Neg1nStatus(block, done, notDone)
    }
}

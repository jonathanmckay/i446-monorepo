package com.mckay.neg1n.phone

import okhttp3.OkHttpClient
import okhttp3.Request
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

    private fun parse(body: String): Neg1nStatus {
        val json = JSONObject(body)
        if (json.has("error")) throw IllegalStateException(json.getString("error"))
        val block = if (json.isNull("block")) null else json.optString("block", null)
        val done = json.optJSONArray("done")?.let { arr ->
            (0 until arr.length()).map { arr.getString(it) }
        } ?: emptyList()
        val notDone = json.optJSONArray("not_done")?.let { arr ->
            (0 until arr.length()).map { arr.getString(it) }
        } ?: emptyList()
        return Neg1nStatus(block, done, notDone)
    }
}

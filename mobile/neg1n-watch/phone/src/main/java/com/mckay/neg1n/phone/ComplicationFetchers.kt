package com.mckay.neg1n.phone

import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/** Tiny GET-only fetchers for the day-points/hcb/hcmp complications —
 * siblings of StatusFetcher's -1n fetch, kept as their own small typed
 * classes rather than one generic JSON-blob fetcher (see the rubber-duck
 * pass on this feature): with only 3 fixed shapes, a shared generic parser
 * would trade compile-time key-name safety for not much less code, and a
 * typo'd DataMap key is exactly the failure mode that's easy to ship
 * unnoticed. [SimpleGetClient] only factors out the identical
 * OkHttpClient/GET-and-parse boilerplate, not the response shapes. */
private object SimpleGetClient {
    val client: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(8, TimeUnit.SECONDS)
        .readTimeout(8, TimeUnit.SECONDS)
        .build()

    /** Blocking GET + JSON parse — background thread/WorkManager only. */
    fun getJson(url: String): Result<JSONObject> = try {
        val request = Request.Builder().url(url).get().build()
        client.newCall(request).execute().use { resp ->
            if (!resp.isSuccessful) {
                Result.failure(IllegalStateException("HTTP ${resp.code}"))
            } else {
                Result.success(JSONObject(resp.body?.string() ?: "{}"))
            }
        }
    } catch (e: Exception) {
        Result.failure(e)
    }
}

/** Result of a fetch against tools/watch/neg1n_status.py's /api/day-points.
 * `points` is null when today's row hasn't landed in the 30min cache yet
 * (e.g. just after midnight) — the arc renderer draws an empty track for
 * that, not a crash. */
data class DayPointsStatus(val points: Int?, val max: Int)

class DayPointsFetcher(private val url: String) {
    fun fetch(): Result<DayPointsStatus> = SimpleGetClient.getJson(url).map { json ->
        DayPointsStatus(
            points = if (json.isNull("points")) null else json.optInt("points"),
            max = json.optInt("max", 1440),
        )
    }
}

/** Result of a fetch against .../api/hcb. `hcbpHcbc`/`goal` are the running
 * Q2+Q3 combined score, not a per-day figure — see the server route's own
 * comment. */
data class HcbStatus(val calories: Int?, val hcbpHcbc: Int?, val goal: Int)

class HcbFetcher(private val url: String) {
    fun fetch(): Result<HcbStatus> = SimpleGetClient.getJson(url).map { json ->
        HcbStatus(
            calories = if (json.isNull("calories")) null else json.optInt("calories"),
            hcbpHcbc = if (json.isNull("hcbp_hcbc")) null else json.optInt("hcbp_hcbc"),
            goal = json.optInt("goal", 131),
        )
    }
}

/** Result of a fetch against .../api/hcmp. */
data class HcmpStatus(val prayers: Int?, val hcmpMinutes: Int?)

class HcmpFetcher(private val url: String) {
    fun fetch(): Result<HcmpStatus> = SimpleGetClient.getJson(url).map { json ->
        HcmpStatus(
            prayers = if (json.isNull("prayers")) null else json.optInt("prayers"),
            hcmpMinutes = if (json.isNull("hcmp_minutes")) null else json.optInt("hcmp_minutes"),
        )
    }
}

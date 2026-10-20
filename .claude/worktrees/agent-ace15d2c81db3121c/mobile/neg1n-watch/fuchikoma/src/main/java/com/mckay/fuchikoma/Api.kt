package com.mckay.fuchikoma

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/** One JSON round-trip to Ix. Never throws: transport failures come back as
 * ApiResult.Error so every caller renders them (inline in the header, not
 * only a toast — a wedged tailnet otherwise looks like an empty day). */
sealed class ApiResult {
    data class Ok(val json: JSONObject) : ApiResult()
    data class Error(val message: String) : ApiResult()
}

class Api {
    private val client = OkHttpClient.Builder()
        .connectTimeout(8, TimeUnit.SECONDS)
        // /api/done and /api/log run did-fast.py (Todoist + Neon over ssh) —
        // several seconds on a good day. Reads must outlast that.
        .readTimeout(45, TimeUnit.SECONDS)
        .build()

    /** Blocking. Call off the main thread. */
    fun get(url: String): ApiResult = run(Request.Builder().url(url).get().build())

    /** Blocking. Call off the main thread. */
    fun post(url: String, body: JSONObject): ApiResult = run(
        Request.Builder().url(url)
            .post(body.toString().toRequestBody("application/json".toMediaType()))
            .build())

    private fun run(req: Request): ApiResult = try {
        client.newCall(req).execute().use { resp ->
            val text = resp.body?.string().orEmpty()
            val json = try { JSONObject(text) } catch (e: Exception) { null }
            when {
                json == null -> ApiResult.Error("HTTP ${resp.code}: not JSON")
                // Flask handlers answer {ok:false, error:...} with 4xx/5xx AND
                // sometimes with 200; trust the body's own verdict first.
                !json.optBoolean("ok", resp.isSuccessful) ->
                    ApiResult.Error(json.optString("error").ifBlank { "HTTP ${resp.code}" })
                else -> ApiResult.Ok(json)
            }
        }
    } catch (e: Exception) {
        ApiResult.Error(e.message ?: e.javaClass.simpleName)
    }
}

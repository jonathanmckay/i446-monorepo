package com.mckay.neg1n.phone

import android.content.Context
import android.util.Log
import androidx.work.CoroutineWorker
import androidx.work.Data
import androidx.work.WorkerParameters

private const val TAG = "Neg1n"
const val KEY_GOAL_TEXT = "goal_text"

/** Sets the current block's -1g goal from text typed/dictated on the watch
 * (RitualListActivity tap on the -1g row -> "/neg1n_goal" ->
 * RitualActionListenerService -> here). Mirrors CompleteRitualWorker: the
 * HTTP call happens on the phone (Tailscale), the returned status goes
 * straight to the watch's Data Layer.
 *
 * Never retried: the server call is not idempotent enough to run twice
 * (a retry after a slow-but-successful first call would append the goal a
 * second time to Todoist). A genuine failure just surfaces as the -1g row
 * coming back on the watch's next reconcile reload. */
class SetGoalWorker(appContext: Context, params: WorkerParameters) :
    CoroutineWorker(appContext, params) {

    override suspend fun doWork(): Result {
        val text = inputData.getString(KEY_GOAL_TEXT)?.trim().orEmpty()
        if (text.isEmpty()) return Result.failure()
        val prefs = applicationContext.getSharedPreferences(Neg1nConfig.PREFS_NAME, Context.MODE_PRIVATE)
        val endpoint = prefs.getString(Neg1nConfig.PREF_ENDPOINT, Neg1nConfig.DEFAULT_ENDPOINT)!!
        Log.i(TAG, "SetGoalWorker: setting goal via $endpoint: $text")
        val status = StatusFetcher(endpoint).setGoal(text).getOrElse { e ->
            Log.e(TAG, "SetGoalWorker: goal failed", e)
            return Result.failure()
        }
        val confirmedAt = System.currentTimeMillis()
        Log.i(TAG, "SetGoalWorker: OK done=${status.done} not_done=${status.notDone}")
        return try {
            DataLayerPush.push(applicationContext, status, endpoint, confirmedAt)
            Result.success()
        } catch (e: Exception) {
            Log.e(TAG, "SetGoalWorker: Data Layer push failed after setting goal", e)
            Result.success()
        }
    }
}

fun setGoalInputData(text: String): Data =
    Data.Builder().putString(KEY_GOAL_TEXT, text).build()

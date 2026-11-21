package com.mckay.neg1n.wear

import android.app.Activity
import android.os.Bundle
import android.util.Log
import android.widget.TextView
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

private const val TAG = "Neg1n"

/** Trivial debug screen — the complication itself is the real UI. Pulls
 * live from DataLayerReader on each launch (not just StatusStore's cache)
 * so this doubles as a way to verify the Data Layer pull actually works —
 * `adb shell am start -n com.mckay.neg1n.wear/.MainActivity` then
 * `adb logcat -d | grep Neg1n` shows the outcome without reading the
 * screen. */
class MainActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        NudgeScheduler.arm(applicationContext)
        requestNudgePermissionIfNeeded()
        val textView = findViewById<TextView>(R.id.statusText)
        textView.text = "-1n\nloading..."
        // `install-wear.sh test HH:MM` launches with this extra.
        val testAt = intent?.getLongExtra(EXTRA_TEST_NUDGE_AT, 0L) ?: 0L
        if (testAt > 0L && NudgeScheduler.armTest(applicationContext, testAt)) {
            val hhmm = java.time.Instant.ofEpochMilli(testAt).atZone(java.time.ZoneId.systemDefault())
                .toLocalTime().toString().take(5)
            textView.text = "-1n\ntest nudge at $hhmm"
            return
        }
        CoroutineScope(Dispatchers.Main).launch {
            val status = DataLayerReader.readLatest(applicationContext)
            val text = if (status.block == null) {
                "-1n\noutside block hours"
            } else {
                "-1n block ${status.block}\n${BarRenderer.summaryText(status.done, status.notDone)}"
            }
            Log.i(TAG, "Wear MainActivity: DataLayerReader -> block=${status.block} " +
                "done=${status.done} not_done=${status.notDone} updatedAt=${status.updatedAtMillis}")
            textView.text = text
        }
    }
}

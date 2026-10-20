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
        val textView = findViewById<TextView>(R.id.statusText)
        textView.text = "-1n\nloading..."
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

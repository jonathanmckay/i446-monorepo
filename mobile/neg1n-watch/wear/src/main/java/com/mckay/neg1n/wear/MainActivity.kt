package com.mckay.neg1n.wear

import android.app.Activity
import android.os.Bundle
import android.widget.TextView

/** Trivial debug screen — the complication itself is the real UI. Shows
 * whatever status was last synced from the phone, for verifying the sync
 * pipeline without needing to look at a watch face. */
class MainActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        val status = StatusStore.load(applicationContext)
        val text = if (status.block == null) {
            "-1n\noutside block hours"
        } else {
            "-1n block ${status.block}\n${BarRenderer.summaryText(status.done, status.notDone)}"
        }
        findViewById<TextView>(R.id.statusText).text = text
    }
}

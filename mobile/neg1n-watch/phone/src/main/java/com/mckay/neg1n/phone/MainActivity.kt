package com.mckay.neg1n.phone

import android.content.Context
import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** Minimal config/debug UI — the real display is the watch complication.
 * This screen only exists to (a) let the endpoint be changed without a
 * rebuild if "ix" doesn't resolve on this phone's network, and (b) trigger
 * an immediate sync for testing. */
class MainActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        val prefs = getSharedPreferences(Neg1nConfig.PREFS_NAME, Context.MODE_PRIVATE)
        val endpointInput = findViewById<EditText>(R.id.endpointInput)
        val statusText = findViewById<TextView>(R.id.statusText)
        endpointInput.setText(prefs.getString(Neg1nConfig.PREF_ENDPOINT, Neg1nConfig.DEFAULT_ENDPOINT))

        findViewById<Button>(R.id.syncNowButton).setOnClickListener {
            val url = endpointInput.text.toString().ifBlank { Neg1nConfig.DEFAULT_ENDPOINT }
            prefs.edit().putString(Neg1nConfig.PREF_ENDPOINT, url).apply()
            statusText.text = "Syncing..."
            lifecycleScope.launch {
                val result = withContext(Dispatchers.IO) { StatusFetcher(url).fetch() }
                statusText.text = result.fold(
                    onSuccess = { s -> "block=${s.block ?: "(none)"}\ndone=${s.done}\nnot_done=${s.notDone}" },
                    onFailure = { e -> "Fetch failed: ${e.message}" },
                )
                SyncScheduler.syncNow(this@MainActivity)
            }
        }

        SyncScheduler.schedulePeriodic(this)
    }
}

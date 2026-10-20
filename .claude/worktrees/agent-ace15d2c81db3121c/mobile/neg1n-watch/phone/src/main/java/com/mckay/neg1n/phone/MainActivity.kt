package com.mckay.neg1n.phone

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.util.Log
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

private const val TAG = "Neg1n"

/** Minimal config/debug UI — the real display is the watch complication.
 * This screen only exists to (a) let the endpoint be changed without a
 * rebuild if "ix" doesn't resolve on this phone's network, and (b) trigger
 * an immediate sync for testing.
 *
 * Debug hook (no UI interaction needed — driveable headlessly via adb):
 *   adb shell am start -n com.mckay.neg1n.phone/.MainActivity \
 *       --es endpoint "http://192.168.1.61:5562/api/neg1n"
 * saves the given endpoint to prefs and runs a sync immediately; the
 * outcome (success + status, or the exact exception) goes to logcat under
 * tag "Neg1n" — `adb logcat -d | grep Neg1n` — instead of needing anyone to
 * read the screen. */
class MainActivity : AppCompatActivity() {

    private lateinit var prefs: android.content.SharedPreferences
    private lateinit var endpointInput: EditText
    private lateinit var statusText: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        prefs = getSharedPreferences(Neg1nConfig.PREFS_NAME, Context.MODE_PRIVATE)
        endpointInput = findViewById(R.id.endpointInput)
        statusText = findViewById(R.id.statusText)
        endpointInput.setText(prefs.getString(Neg1nConfig.PREF_ENDPOINT, Neg1nConfig.DEFAULT_ENDPOINT))

        findViewById<Button>(R.id.syncNowButton).setOnClickListener {
            runSync(endpointInput.text.toString().ifBlank { Neg1nConfig.DEFAULT_ENDPOINT })
        }

        SyncScheduler.schedulePeriodic(this)
        handleEndpointExtra(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        // am start hits this instead of onCreate whenever MainActivity is
        // already the top of its task (e.g. a repeat headless debug trigger
        // via `adb shell am start ... --es endpoint ...`) — without this
        // override the extra was silently dropped and nothing ran.
        setIntent(intent)
        handleEndpointExtra(intent)
    }

    private fun handleEndpointExtra(intent: Intent?) {
        intent?.getStringExtra("endpoint")?.let { url ->
            Log.i(TAG, "MainActivity (re)started with --es endpoint=\"$url\" — running headless sync")
            endpointInput.setText(url)
            runSync(url)
        }
    }

    private fun runSync(url: String) {
        prefs.edit().putString(Neg1nConfig.PREF_ENDPOINT, url).apply()
        statusText.text = "Syncing..."
        Log.i(TAG, "Sync starting: url=$url")
        lifecycleScope.launch {
            val result = withContext(Dispatchers.IO) { StatusFetcher(url).fetch() }
            result.fold(
                onSuccess = { s ->
                    val msg = "block=${s.block ?: "(none)"} done=${s.done} not_done=${s.notDone}"
                    Log.i(TAG, "Sync OK: $msg")
                    statusText.text = msg
                },
                onFailure = { e ->
                    Log.e(TAG, "Sync FAILED: url=$url", e)
                    statusText.text = "Fetch failed: ${e.javaClass.simpleName}: ${e.message}"
                },
            )
            SyncScheduler.syncNow(this@MainActivity)
        }
    }
}

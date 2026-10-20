package com.mckay.fuchikoma

import android.content.Intent
import android.os.Bundle
import android.text.InputType
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.fragment.app.Fragment
import com.google.android.material.bottomnavigation.BottomNavigationView

/** Two surfaces, one app: dtd (tasks) and janus (timeline). Fragments are
 * kept alive and shown/hidden so tab flips never refetch. The header is
 * shared and owned here; each fragment pushes its own line into it. */
class MainActivity : AppCompatActivity() {

    private lateinit var headerTitle: TextView
    private lateinit var headerStatus: TextView
    private lateinit var nav: BottomNavigationView
    private val dtd = DtdFragment()
    private val janus = JanusFragment()
    private var current: Fragment? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        headerTitle = findViewById(R.id.headerTitle)
        headerStatus = findViewById(R.id.headerStatus)
        nav = findViewById(R.id.bottomNav)

        supportFragmentManager.beginTransaction()
            .add(R.id.container, janus, "janus").hide(janus)
            .add(R.id.container, dtd, "dtd").hide(dtd)
            .commitNow()

        nav.setOnItemSelectedListener { item ->
            show(if (item.itemId == R.id.nav_janus) janus else dtd); true
        }
        findViewById<LinearLayout>(R.id.header).setOnLongClickListener { editEndpoints(); true }
        selectFromIntent(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        selectFromIntent(intent)
    }

    private fun selectFromIntent(intent: Intent?) {
        val tab = intent?.getStringExtra(FuchikomaConfig.EXTRA_TAB)
        nav.selectedItemId = if (tab == FuchikomaConfig.TAB_JANUS) R.id.nav_janus else R.id.nav_dtd
        // selectedItemId only fires the listener on a CHANGE; force the
        // first show when the default tab is already selected.
        if (current == null) show(if (tab == FuchikomaConfig.TAB_JANUS) janus else dtd)
    }

    private fun show(f: Fragment) {
        if (current === f) return
        val tx = supportFragmentManager.beginTransaction()
        current?.let { tx.hide(it) }
        tx.show(f).commitNow()
        current = f
        (f as? Surface)?.onShown()
    }

    /** Fragments push their status line here. */
    fun setHeader(from: Fragment, title: String, status: String) {
        if (current !== from) return
        headerTitle.text = title
        headerStatus.text = status
    }

    private fun editEndpoints() {
        val prefs = FuchikomaConfig.prefs(this)
        val col = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(48, 24, 48, 0) }
        val dtdIn = EditText(this).apply { hint = "dtd base"; inputType = InputType.TYPE_TEXT_VARIATION_URI
            setText(FuchikomaConfig.dtdBase(this@MainActivity)) }
        val janusIn = EditText(this).apply { hint = "janus base"; inputType = InputType.TYPE_TEXT_VARIATION_URI
            setText(FuchikomaConfig.janusBase(this@MainActivity)) }
        col.addView(dtdIn); col.addView(janusIn)
        AlertDialog.Builder(this).setTitle(getString(R.string.settings)).setView(col)
            .setPositiveButton("save") { _, _ ->
                prefs.edit()
                    .putString(FuchikomaConfig.PREF_DTD, dtdIn.text.toString().trim())
                    .putString(FuchikomaConfig.PREF_JANUS, janusIn.text.toString().trim())
                    .apply()
                (current as? Surface)?.reload()
            }
            .setNeutralButton("defaults") { _, _ ->
                prefs.edit().remove(FuchikomaConfig.PREF_DTD).remove(FuchikomaConfig.PREF_JANUS).apply()
                (current as? Surface)?.reload()
            }
            .setNegativeButton("cancel", null).show()
    }
}

/** What the activity needs from either tab. */
interface Surface {
    fun onShown()
    fun reload()
}

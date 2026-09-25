package com.mckay.fuchikoma

import android.content.Context
import android.content.SharedPreferences

/** Endpoints for the two Flask services on Ix. Same reasoning as the neg1n
 * phone app's Neg1nConfig.DEFAULT_ENDPOINT: Ix's Tailscale IP, not the "ix"
 * MagicDNS name or a LAN IP, so it routes from cellular / foreign WiFi as
 * long as Tailscale is up on the phone. Both are editable in-app (long-press
 * the header) and persisted, so a moved service never needs a rebuild. */
object FuchikomaConfig {
    const val PREFS_NAME = "fuchikoma_prefs"
    const val PREF_DTD = "dtd_base"
    const val PREF_JANUS = "janus_base"
    const val DEFAULT_DTD = "http://100.114.46.109:5560"
    const val DEFAULT_JANUS = "http://100.114.46.109:5561"

    /** Launcher-shortcut / intent extra selecting the initial tab. */
    const val EXTRA_TAB = "tab"
    const val TAB_DTD = "dtd"
    const val TAB_JANUS = "janus"

    /** Fixed swipe commit distance, in dp. Proportional thresholds are wrong on
     * a trifold: unfolded, the row is ~3 panels wide and a 45%-of-width swipe
     * is a whole thumb-length; folded it is a twitch. */
    const val SWIPE_COMMIT_DP = 160f

    fun prefs(ctx: Context): SharedPreferences = ctx.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    fun dtdBase(ctx: Context): String = prefs(ctx).getString(PREF_DTD, DEFAULT_DTD)!!.trimEnd('/')
    fun janusBase(ctx: Context): String = prefs(ctx).getString(PREF_JANUS, DEFAULT_JANUS)!!.trimEnd('/')
}

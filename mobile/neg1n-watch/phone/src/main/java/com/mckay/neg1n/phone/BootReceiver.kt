package com.mckay.neg1n.phone

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** Re-arms the periodic sync after a reboot — WorkManager's own persistence
 * normally survives reboots on its own, but this makes the dependency
 * explicit and gives a syncNow() kick on first boot. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED) {
            SyncScheduler.schedulePeriodic(context)
            SyncScheduler.syncNow(context)
        }
    }
}

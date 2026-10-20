package com.mckay.neg1n.phone

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import java.util.concurrent.TimeUnit

object SyncScheduler {

    /** Wear complications only need to be roughly current — a -1n block is
     * 2 hours long, so a 15-minute floor (WorkManager's minimum periodic
     * interval) gives 8 refreshes per block without being wasteful. */
    fun schedulePeriodic(context: Context) {
        val constraints = Constraints.Builder()
            .setRequiredNetworkType(NetworkType.CONNECTED)
            .build()
        val request = PeriodicWorkRequestBuilder<StatusSyncWorker>(15, TimeUnit.MINUTES)
            .setConstraints(constraints)
            .setBackoffCriteria(BackoffPolicy.LINEAR, 1, TimeUnit.MINUTES)
            .build()
        WorkManager.getInstance(context).enqueueUniquePeriodicWork(
            Neg1nConfig.SYNC_WORK_NAME,
            ExistingPeriodicWorkPolicy.KEEP,
            request,
        )
    }

    /** Manual "sync now" (MainActivity button) — runs immediately regardless
     * of the periodic schedule's cadence. */
    fun syncNow(context: Context) {
        val request = OneTimeWorkRequestBuilder<StatusSyncWorker>().build()
        WorkManager.getInstance(context).enqueueUniqueWork(
            "${Neg1nConfig.SYNC_WORK_NAME}_manual",
            ExistingWorkPolicy.REPLACE,
            request,
        )
    }
}

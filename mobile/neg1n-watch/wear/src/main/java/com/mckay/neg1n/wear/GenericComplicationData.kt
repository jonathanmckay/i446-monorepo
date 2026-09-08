package com.mckay.neg1n.wear

import android.content.Context
import android.net.Uri
import android.util.Log
import com.google.android.gms.wearable.DataMap
import com.google.android.gms.wearable.DataMapItem
import com.google.android.gms.wearable.Wearable
import kotlinx.coroutines.tasks.await
import kotlinx.coroutines.withTimeoutOrNull

private const val TAG = "Neg1n"

/** Shared "read this Data Layer path live" primitive for the day-points/hcb/
 * hcmp complications, kept deliberately separate from DataLayerReader (the
 * -1n-specific reader) rather than folding this in as a generalization of
 * it: -1n's reader carries real behavior these three don't need (a "remote
 * vs local, whichever is newer" race against RitualCompleter's on-watch
 * writes, since only -1n has a watch-side write path at all) and is itself
 * under active development — reusing just this low-level read mechanic here
 * is far lower-risk than threading 3 new callers through code that's still
 * moving.
 *
 * Deliberately returns a raw `DataMap?` (null on any failure/miss) rather
 * than also owning a generic cache fallback: `DataMap` has no documented
 * string round-trip, so each complication service caches its own small,
 * explicitly-typed field set instead (2-3 SharedPrefs puts/gets — see e.g.
 * DayPointsComplicationService) rather than this file guessing at a generic
 * (de)serialization. That per-service cache IS the platformized part
 * that matters (the Data Layer read + wildcard-Uri + timeout dance below);
 * three tiny typed caches on top of it is a fine, much safer trade against
 * one fragile generic one. */
object GenericComplicationData {

    suspend fun readRemote(context: Context, path: String): DataMap? {
        try {
            val dataClient = Wearable.getDataClient(context)
            // See DataLayerReader.kt's identical comment: the literal "*"
            // wildcard host is required to match an item synced from
            // another node (the phone), not an authority-less Uri.
            val uri = Uri.parse("wear://*$path")
            val buffer = withTimeoutOrNull(3000) { dataClient.getDataItems(uri).await() }
            if (buffer == null) {
                Log.e(TAG, "GenericComplicationData: getDataItems timed out for $path")
                return null
            }
            try {
                if (buffer.count > 0) {
                    return DataMapItem.fromDataItem(buffer[0]).dataMap
                }
            } finally {
                buffer.release()
            }
        } catch (e: Exception) {
            Log.e(TAG, "GenericComplicationData: live read failed for $path", e)
        }
        return null
    }
}

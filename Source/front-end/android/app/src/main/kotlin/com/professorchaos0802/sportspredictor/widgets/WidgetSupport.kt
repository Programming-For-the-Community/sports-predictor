package com.professorchaos0802.sportspredictor.widgets

import android.content.Context
import android.content.SharedPreferences
import android.net.Uri
import android.widget.RemoteViews
import com.professorchaos0802.sportspredictor.MainActivity
import es.antonborri.home_widget.HomeWidgetLaunchIntent
import org.json.JSONObject

/** The sport a placed widget shows -- written by widget_setup_page.dart. */
internal fun SharedPreferences.sportFor(widgetId: Int): String? = getString("widget_sport_$widgetId", null)

/** Data written by widget_sync.dart, or null when it hasn't run yet. */
internal fun SharedPreferences.json(key: String): JSONObject? =
    getString(key, null)?.let { runCatching { JSONObject(it) }.getOrNull() }

/** A tap opens [route] in the app (lib/app.dart reads sportspredictor://open?route=...). */
internal fun RemoteViews.openOnTap(context: Context, viewId: Int, route: String?) {
    val uri = Uri.Builder().scheme("sportspredictor").authority("open")
        .apply { if (route != null) appendQueryParameter("route", route) }
        .build()
    setOnClickPendingIntent(viewId, HomeWidgetLaunchIntent.getActivity(context, MainActivity::class.java, uri))
}

internal fun percent(value: Double): String = "${Math.round(value * 100)}%"

private val sparkBlocks = charArrayOf('▁', '▂', '▃', '▄', '▅', '▆', '▇', '█')

/** Accuracy values (0..1) as block characters scaled between their own min and max. */
internal fun sparkline(values: List<Double>): String {
    if (values.isEmpty()) return ""
    val lo = values.min()
    val span = (values.max() - lo).takeIf { it > 0 } ?: 1.0
    return values.joinToString("") { sparkBlocks[(((it - lo) / span) * (sparkBlocks.size - 1)).toInt()].toString() }
}

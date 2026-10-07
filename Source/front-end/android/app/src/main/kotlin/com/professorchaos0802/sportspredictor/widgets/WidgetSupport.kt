package com.professorchaos0802.sportspredictor.widgets

import android.appwidget.AppWidgetManager
import android.content.Context
import android.content.SharedPreferences
import android.content.res.Configuration
import android.net.Uri
import android.os.Bundle
import android.text.SpannableStringBuilder
import android.text.Spanned
import android.text.style.ForegroundColorSpan
import android.util.TypedValue
import android.widget.RemoteViews
import com.professorchaos0802.sportspredictor.MainActivity
import es.antonborri.home_widget.HomeWidgetLaunchIntent
import es.antonborri.home_widget.HomeWidgetProvider
import org.json.JSONObject

private const val MAX_TEXT_SCALE = 1.4f

/** A widget that draws itself again when it's resized, so what it shows can follow its size. */
abstract class ResizableWidgetProvider : HomeWidgetProvider() {
    override fun onAppWidgetOptionsChanged(
        context: Context,
        appWidgetManager: AppWidgetManager,
        appWidgetId: Int,
        newOptions: Bundle,
    ) {
        onUpdate(context, appWidgetManager, intArrayOf(appWidgetId))
    }
}

/**
 * A size in dp: a placed widget's, or the smallest a layout's contents fit at the text sizes in
 * res/values/widget_dimens.xml.
 */
internal class WidgetSize(val width: Float, val height: Float)

/** The size the launcher drew [widgetId] at, or null when it reports none. */
internal fun AppWidgetManager.sizeOf(context: Context, widgetId: Int): WidgetSize? {
    val options = getAppWidgetOptions(widgetId)
    // Launchers report the portrait size as min width x max height, landscape as max width x min height.
    val portrait = context.resources.configuration.orientation != Configuration.ORIENTATION_LANDSCAPE
    val width = options.getInt(
        if (portrait) AppWidgetManager.OPTION_APPWIDGET_MIN_WIDTH else AppWidgetManager.OPTION_APPWIDGET_MAX_WIDTH,
    )
    val height = options.getInt(
        if (portrait) AppWidgetManager.OPTION_APPWIDGET_MAX_HEIGHT else AppWidgetManager.OPTION_APPWIDGET_MIN_HEIGHT,
    )
    return if (width > 0 && height > 0) WidgetSize(width.toFloat(), height.toFloat()) else null
}

/** How far text can grow past a layout's [fit] size in a widget of this size, 1 when the size is unknown. */
internal fun WidgetSize?.textScale(fit: WidgetSize): Float =
    if (this == null) 1f else minOf(width / fit.width, height / fit.height).coerceIn(1f, MAX_TEXT_SCALE)

/** Draws each view in [sizes] at its text size dimen times [scale]. */
internal fun RemoteViews.scaleText(context: Context, scale: Float, sizes: Map<Int, List<Int>>) {
    for ((dimen, viewIds) in sizes) {
        val size = context.resources.getDimension(dimen) * scale
        for (viewId in viewIds) setTextViewTextSize(viewId, TypedValue.COMPLEX_UNIT_PX, size)
    }
}

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

/** [parts] joined into one line, each in its own color. */
internal fun colored(vararg parts: Pair<String, Int>): CharSequence {
    val text = SpannableStringBuilder()
    for ((part, color) in parts) {
        val start = text.length
        text.append(part)
        text.setSpan(ForegroundColorSpan(color), start, text.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
    }
    return text
}

package com.professorchaos0802.sportspredictor.widgets

import android.appwidget.AppWidgetManager
import android.content.Context
import android.content.SharedPreferences
import android.view.View
import android.widget.RemoteViews
import com.professorchaos0802.sportspredictor.R
import es.antonborri.home_widget.HomeWidgetProvider

/** One sport's win-probability accuracy: season figure, last period, trend. */
class ModelAccuracyWidget : HomeWidgetProvider() {
    override fun onUpdate(
        context: Context,
        appWidgetManager: AppWidgetManager,
        appWidgetIds: IntArray,
        widgetData: SharedPreferences,
    ) {
        for (widgetId in appWidgetIds) {
            appWidgetManager.updateAppWidget(widgetId, render(context, widgetData, widgetId))
        }
    }

    private fun render(context: Context, widgetData: SharedPreferences, widgetId: Int): RemoteViews {
        val views = RemoteViews(context.packageName, R.layout.widget_model_accuracy)
        val sport = widgetData.sportFor(widgetId)
        val data = sport?.let { widgetData.json("accuracy_$it") }
        views.openOnTap(context, R.id.widget_root, data?.optString("route")?.takeIf { it.isNotEmpty() })

        val message = when {
            sport == null -> context.getString(R.string.widget_choose_sport)
            data == null -> context.getString(R.string.widget_loading)
            data.has("empty") -> data.getString("empty")
            else -> null
        }
        val dataViews = intArrayOf(R.id.acc_big, R.id.acc_season, R.id.acc_last, R.id.acc_trend)
        if (data == null || message != null) {
            views.setTextViewText(R.id.acc_header, data?.optString("sport") ?: context.getString(R.string.widget_accuracy_label))
            views.setTextViewText(R.id.acc_message, message)
            views.setViewVisibility(R.id.acc_message, View.VISIBLE)
            dataViews.forEach { views.setViewVisibility(it, View.GONE) }
            return views
        }
        views.setViewVisibility(R.id.acc_message, View.GONE)
        dataViews.forEach { views.setViewVisibility(it, View.VISIBLE) }
        views.setTextViewText(R.id.acc_header, "${data.getString("sport")} · ${data.getString("model")}")
        views.setTextViewText(R.id.acc_big, percent(data.getDouble("season_pct")))
        views.setTextViewText(R.id.acc_season, "right this season · ${data.getInt("season_n")}")
        if (data.isNull("last_pct")) {
            views.setViewVisibility(R.id.acc_last, View.GONE)
        } else {
            val label = data.optString("last_label").ifEmpty { "Last" }
            views.setTextViewText(R.id.acc_last, "$label: ${percent(data.getDouble("last_pct"))} (${data.getInt("last_n")})")
        }
        val trend = data.optJSONArray("trend")
        val values = if (trend == null) emptyList() else (0 until trend.length()).map { trend.getDouble(it) }
        views.setTextViewText(R.id.acc_trend, sparkline(values))
        return views
    }
}

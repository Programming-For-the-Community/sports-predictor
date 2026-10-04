package com.professorchaos0802.sportspredictor.widgets

import android.appwidget.AppWidgetManager
import android.content.Context
import android.content.SharedPreferences
import android.view.View
import android.widget.RemoteViews
import com.professorchaos0802.sportspredictor.R
import es.antonborri.home_widget.HomeWidgetProvider

/** One sport's most confident picks for its next game day, tournament or race. */
class TopPicksWidget : HomeWidgetProvider() {
    private class Row(val row: Int, val label: Int, val value: Int, val bar: Int)

    private val rows = listOf(
        Row(R.id.pick1_row, R.id.pick1_label, R.id.pick1_value, R.id.pick1_bar),
        Row(R.id.pick2_row, R.id.pick2_label, R.id.pick2_value, R.id.pick2_bar),
        Row(R.id.pick3_row, R.id.pick3_label, R.id.pick3_value, R.id.pick3_bar),
    )

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
        val views = RemoteViews(context.packageName, R.layout.widget_top_picks)
        val sport = widgetData.sportFor(widgetId)
        val data = sport?.let { widgetData.json("picks_$it") }
        views.openOnTap(context, R.id.widget_root, data?.optString("route")?.takeIf { it.isNotEmpty() })

        val title = context.getString(R.string.widget_picks_label)
        views.setTextViewText(R.id.picks_sport, data?.optString("sport")?.let { "$title · $it" } ?: title)
        views.setTextViewText(R.id.picks_heading, data?.optString("heading") ?: "")

        val picks = data?.optJSONArray("picks")
        val message = when {
            sport == null -> context.getString(R.string.widget_choose_sport)
            data == null -> context.getString(R.string.widget_loading)
            data.has("empty") -> data.getString("empty")
            picks == null || picks.length() == 0 -> context.getString(R.string.widget_loading)
            else -> null
        }
        views.setViewVisibility(R.id.picks_message, if (message != null) View.VISIBLE else View.GONE)
        if (message != null) views.setTextViewText(R.id.picks_message, message)

        rows.forEachIndexed { index, row ->
            val pick = if (message == null) picks?.optJSONObject(index) else null
            if (pick == null) {
                views.setViewVisibility(row.row, View.GONE)
                return@forEachIndexed
            }
            views.setViewVisibility(row.row, View.VISIBLE)
            views.setTextViewText(row.label, pick.getString("label"))
            views.setTextViewText(row.value, pick.getString("value"))
            views.setProgressBar(row.bar, 100, Math.round(pick.getDouble("pct") * 100).toInt(), false)
        }
        return views
    }
}

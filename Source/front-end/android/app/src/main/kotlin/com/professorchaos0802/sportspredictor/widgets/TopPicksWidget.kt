package com.professorchaos0802.sportspredictor.widgets

import android.appwidget.AppWidgetManager
import android.content.Context
import android.content.SharedPreferences
import android.widget.RemoteViews
import com.professorchaos0802.sportspredictor.R

/** One sport's most confident picks for its next game day, tournament or race -- as many as the widget's height holds. */
class TopPicksWidget : ResizableWidgetProvider() {
    override fun onUpdate(
        context: Context,
        appWidgetManager: AppWidgetManager,
        appWidgetIds: IntArray,
        widgetData: SharedPreferences,
    ) {
        for (widgetId in appWidgetIds) {
            val size = appWidgetManager.sizeOf(context, widgetId)
            val scale = size.textScale(fit)
            val stretch = textStretch(context, scale)
            val rows = size?.let { picksThatFit(rowsHeight(it, PADDING, stretch), stretch) } ?: UNSIZED_ROWS
            val views = render(context, widgetData, widgetId, rows)
            views.scaleText(context, scale, textSizes)
            appWidgetManager.updateAppWidget(widgetId, views)
        }
    }

    private fun render(context: Context, widgetData: SharedPreferences, widgetId: Int, rows: Int): RemoteViews {
        val views = RemoteViews(context.packageName, R.layout.widget_top_picks)
        val sport = widgetData.sportFor(widgetId)
        val data = sport?.let { widgetData.json("picks_$it") }
        views.openOnTap(context, R.id.widget_root, data?.optString("route")?.takeIf { it.isNotEmpty() })
        views.bindPicksHeader(context.getString(R.string.widget_picks_label), data)

        val picks = data?.optJSONArray("picks")
        val message = picksMessage(context, sport, data, picks, R.string.widget_loading)
        views.bindPicksMessage(message)
        views.bindPicks(if (message == null) picks else null, rows)
        return views
    }

    private companion object {
        /** The layout's padding in dp. */
        const val PADDING = 14f

        /** Rows shown when the launcher reports no size. */
        const val UNSIZED_ROWS = 3
        val fit = WidgetSize(270f, 75f)
        val textSizes = mapOf(
            R.dimen.widget_text_row to pickRows.map { it.label },
            R.dimen.widget_text_body to pickRows.map { it.value } + listOf(R.id.picks_sport, R.id.picks_message),
            R.dimen.widget_text_meta to listOf(R.id.picks_heading),
        )
    }
}

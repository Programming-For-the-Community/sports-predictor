package com.professorchaos0802.sportspredictor.widgets

import android.appwidget.AppWidgetManager
import android.content.Context
import android.content.SharedPreferences
import android.widget.RemoteViews
import com.professorchaos0802.sportspredictor.R

/**
 * The player projections the model has been closest on for one sport's next game day -- as many
 * as the widget's height holds.
 */
class TopPropsWidget : ResizableWidgetProvider() {
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
            val rows = size?.let { propsThatFit(rowsHeight(it, PADDING, stretch), stretch) } ?: UNSIZED_ROWS
            val views = render(context, widgetData, widgetId, rows)
            views.scaleText(context, scale, textSizes)
            appWidgetManager.updateAppWidget(widgetId, views)
        }
    }

    private fun render(context: Context, widgetData: SharedPreferences, widgetId: Int, rows: Int): RemoteViews {
        val views = RemoteViews(context.packageName, R.layout.widget_top_props)
        val sport = widgetData.sportFor(widgetId)
        val data = sport?.let { widgetData.json("picks_$it") }
        views.openOnTap(context, R.id.widget_root, data?.optString("route")?.takeIf { it.isNotEmpty() })
        views.bindPicksHeader(context.getString(R.string.widget_props_label), data)

        val props = data?.optJSONArray("props")
        val message = picksMessage(context, sport, data, props, R.string.widget_props_none)
        views.bindPicksMessage(message)
        views.bindProps(context, if (message == null) props else null, rows)
        return views
    }

    private companion object {
        /** The layout's padding in dp. */
        const val PADDING = 14f

        /** Rows shown when the launcher reports no size. */
        const val UNSIZED_ROWS = 5
        val fit = WidgetSize(270f, 70f)
        val textSizes = mapOf(
            R.dimen.widget_text_row to propRows.map { it.label },
            R.dimen.widget_text_body to propRows.map { it.value } + listOf(R.id.picks_sport, R.id.picks_message),
            R.dimen.widget_text_meta to listOf(R.id.picks_heading),
        )
    }
}

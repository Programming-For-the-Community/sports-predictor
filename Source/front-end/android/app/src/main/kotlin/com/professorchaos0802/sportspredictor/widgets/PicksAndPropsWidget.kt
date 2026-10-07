package com.professorchaos0802.sportspredictor.widgets

import android.appwidget.AppWidgetManager
import android.content.Context
import android.content.SharedPreferences
import android.view.View
import android.widget.RemoteViews
import com.professorchaos0802.sportspredictor.R

/**
 * One sport's most confident winner picks in the top half and its top player props in the
 * bottom half, each with as many rows as its half holds.
 */
class PicksAndPropsWidget : ResizableWidgetProvider() {
    override fun onUpdate(
        context: Context,
        appWidgetManager: AppWidgetManager,
        appWidgetIds: IntArray,
        widgetData: SharedPreferences,
    ) {
        for (widgetId in appWidgetIds) {
            val size = appWidgetManager.sizeOf(context, widgetId)
            val scale = size.textScale(fit)
            val views = render(context, widgetData, widgetId, size, textStretch(context, scale))
            views.scaleText(context, scale, textSizes)
            appWidgetManager.updateAppWidget(widgetId, views)
        }
    }

    private fun render(
        context: Context,
        widgetData: SharedPreferences,
        widgetId: Int,
        size: WidgetSize?,
        stretch: Float,
    ): RemoteViews {
        val views = RemoteViews(context.packageName, R.layout.widget_picks_and_props)
        val sport = widgetData.sportFor(widgetId)
        val data = sport?.let { widgetData.json("picks_$it") }
        views.openOnTap(context, R.id.widget_root, data?.optString("route")?.takeIf { it.isNotEmpty() })
        views.bindPicksHeader(context.getString(R.string.widget_picks_label), data)

        val picks = data?.optJSONArray("picks")
        val message = picksMessage(context, sport, data, picks, R.string.widget_loading)
        views.bindPicksMessage(message)

        // With no props to list, the winners take the whole widget.
        val props = if (message == null) data?.optJSONArray("props")?.takeIf { it.length() > 0 } else null
        views.setViewVisibility(R.id.props_section, if (props != null) View.VISIBLE else View.GONE)
        val height = size?.let { rowsHeight(it, PADDING, stretch) }
        val pickCount = height?.let { picksThatFit(if (props != null) it / 2 else it, stretch) } ?: UNSIZED_ROWS
        val propCount = height?.let { titledPropsThatFit(it / 2, stretch) } ?: UNSIZED_ROWS
        views.bindPicks(if (message == null) picks else null, pickCount)
        views.bindProps(context, props, propCount)
        return views
    }

    private companion object {
        /** The layout's padding in dp. */
        const val PADDING = 12f

        /** Rows shown in each half when the launcher reports no size. */
        const val UNSIZED_ROWS = 3
        val fit = WidgetSize(270f, 130f)
        val textSizes = mapOf(
            R.dimen.widget_text_row to pickRows.map { it.label } + propRows.map { it.label },
            R.dimen.widget_text_body to pickRows.map { it.value } + propRows.map { it.value } +
                listOf(R.id.picks_sport, R.id.picks_message),
            R.dimen.widget_text_meta to listOf(R.id.picks_heading),
            R.dimen.widget_text_caption to listOf(R.id.props_title),
        )
    }
}

package com.professorchaos0802.sportspredictor.widgets

import android.appwidget.AppWidgetManager
import android.content.Context
import android.content.SharedPreferences
import android.widget.RemoteViews
import com.professorchaos0802.sportspredictor.R
import es.antonborri.home_widget.HomeWidgetProvider

/** The player projections the model has been closest on for one sport's next game day. */
class TopPropsWidget : HomeWidgetProvider() {
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
        val views = RemoteViews(context.packageName, R.layout.widget_top_props)
        val sport = widgetData.sportFor(widgetId)
        val data = sport?.let { widgetData.json("picks_$it") }
        views.openOnTap(context, R.id.widget_root, data?.optString("route")?.takeIf { it.isNotEmpty() })
        views.bindPicksHeader(context.getString(R.string.widget_props_label), data)

        val props = data?.optJSONArray("props")
        val message = picksMessage(context, sport, data, props, R.string.widget_props_none)
        views.bindPicksMessage(message)
        views.bindProps(context, propRows, if (message == null) props else null)
        return views
    }
}

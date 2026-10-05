package com.professorchaos0802.sportspredictor.widgets

import android.appwidget.AppWidgetManager
import android.content.Context
import android.content.SharedPreferences
import android.view.View
import android.widget.RemoteViews
import com.professorchaos0802.sportspredictor.R
import es.antonborri.home_widget.HomeWidgetProvider

/** One sport's most confident winner picks above its top player props. */
class PicksAndPropsWidget : HomeWidgetProvider() {
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
        val views = RemoteViews(context.packageName, R.layout.widget_picks_and_props)
        val sport = widgetData.sportFor(widgetId)
        val data = sport?.let { widgetData.json("picks_$it") }
        views.openOnTap(context, R.id.widget_root, data?.optString("route")?.takeIf { it.isNotEmpty() })
        views.bindPicksHeader(context.getString(R.string.widget_picks_label), data)

        val picks = data?.optJSONArray("picks")
        val message = picksMessage(context, sport, data, picks, R.string.widget_loading)
        views.bindPicksMessage(message)
        views.bindPicks(pickRows, if (message == null) picks else null)

        // With no props to list, the winners stand alone.
        val props = if (message == null) data?.optJSONArray("props")?.takeIf { it.length() > 0 } else null
        views.setViewVisibility(R.id.props_section, if (props != null) View.VISIBLE else View.GONE)
        views.bindProps(context, propRows.take(3), props)
        return views
    }
}

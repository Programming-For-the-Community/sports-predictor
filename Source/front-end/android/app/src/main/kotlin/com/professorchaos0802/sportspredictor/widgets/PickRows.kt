package com.professorchaos0802.sportspredictor.widgets

import android.content.Context
import android.view.View
import android.widget.RemoteViews
import androidx.core.content.ContextCompat
import com.professorchaos0802.sportspredictor.R
import org.json.JSONArray
import org.json.JSONObject

/** One winner pick's views: matchup, pick and its win-chance bar. */
internal class PickRow(val row: Int, val label: Int, val value: Int, val bar: Int)

/** One player prop's views: player and team, then projection and tolerance. */
internal class PropRow(val row: Int, val label: Int, val value: Int)

internal val pickRows = listOf(
    PickRow(R.id.pick1_row, R.id.pick1_label, R.id.pick1_value, R.id.pick1_bar),
    PickRow(R.id.pick2_row, R.id.pick2_label, R.id.pick2_value, R.id.pick2_bar),
    PickRow(R.id.pick3_row, R.id.pick3_label, R.id.pick3_value, R.id.pick3_bar),
)

internal val propRows = listOf(
    PropRow(R.id.prop1_row, R.id.prop1_label, R.id.prop1_value),
    PropRow(R.id.prop2_row, R.id.prop2_label, R.id.prop2_value),
    PropRow(R.id.prop3_row, R.id.prop3_label, R.id.prop3_value),
    PropRow(R.id.prop4_row, R.id.prop4_label, R.id.prop4_value),
    PropRow(R.id.prop5_row, R.id.prop5_label, R.id.prop5_value),
)

/** What a picks widget says instead of its rows, or null when [rows] has some to show. */
internal fun picksMessage(context: Context, sport: String?, data: JSONObject?, rows: JSONArray?, noRows: Int): String? = when {
    sport == null -> context.getString(R.string.widget_choose_sport)
    data == null -> context.getString(R.string.widget_loading)
    data.has("empty") -> data.getString("empty")
    rows == null -> context.getString(R.string.widget_loading)
    rows.length() == 0 -> context.getString(noRows)
    else -> null
}

/** The header both lines of a picks widget share: "[title] · NFL" and the day. */
internal fun RemoteViews.bindPicksHeader(title: String, data: JSONObject?) {
    setTextViewText(R.id.picks_sport, data?.optString("sport")?.let { "$title · $it" } ?: title)
    setTextViewText(R.id.picks_heading, data?.optString("heading") ?: "")
}

internal fun RemoteViews.bindPicksMessage(message: String?) {
    setViewVisibility(R.id.picks_message, if (message != null) View.VISIBLE else View.GONE)
    if (message != null) setTextViewText(R.id.picks_message, message)
}

/** Fills [rows] from [picks]; a row without a pick is hidden. */
internal fun RemoteViews.bindPicks(rows: List<PickRow>, picks: JSONArray?) {
    rows.forEachIndexed { index, row ->
        val pick = picks?.optJSONObject(index)
        if (pick == null) {
            setViewVisibility(row.row, View.GONE)
            return@forEachIndexed
        }
        setViewVisibility(row.row, View.VISIBLE)
        setTextViewText(row.label, pick.getString("label"))
        setTextViewText(row.value, pick.getString("value"))
        setProgressBar(row.bar, 100, Math.round(pick.getDouble("pct") * 100).toInt(), false)
    }
}

/** Fills [rows] from [props]; a row without a prop is hidden. A tap on a row opens that player's game. */
internal fun RemoteViews.bindProps(context: Context, rows: List<PropRow>, props: JSONArray?) {
    val ink = ContextCompat.getColor(context, R.color.widget_ink)
    val sub = ContextCompat.getColor(context, R.color.widget_ink_sub)
    val cyan = ContextCompat.getColor(context, R.color.widget_cyan)
    rows.forEachIndexed { index, row ->
        val prop = props?.optJSONObject(index)
        if (prop == null) {
            setViewVisibility(row.row, View.GONE)
            return@forEachIndexed
        }
        setViewVisibility(row.row, View.VISIBLE)
        val team = if (prop.isNull("team")) "" else " · ${prop.getString("team")}"
        setTextViewText(row.label, colored(prop.getString("name") to ink, team to sub))
        setTextViewText(
            row.value,
            colored(prop.getString("value") to cyan, " ${prop.getString("unit")} " to sub, prop.getString("tolerance") to ink),
        )
        openOnTap(context, row.row, prop.optString("route").takeIf { it.isNotEmpty() })
    }
}

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

/** Every pick row a layout holds; a widget shows as many as its height has room for. */
internal val pickRows = listOf(
    PickRow(R.id.pick1_row, R.id.pick1_label, R.id.pick1_value, R.id.pick1_bar),
    PickRow(R.id.pick2_row, R.id.pick2_label, R.id.pick2_value, R.id.pick2_bar),
    PickRow(R.id.pick3_row, R.id.pick3_label, R.id.pick3_value, R.id.pick3_bar),
    PickRow(R.id.pick4_row, R.id.pick4_label, R.id.pick4_value, R.id.pick4_bar),
    PickRow(R.id.pick5_row, R.id.pick5_label, R.id.pick5_value, R.id.pick5_bar),
    PickRow(R.id.pick6_row, R.id.pick6_label, R.id.pick6_value, R.id.pick6_bar),
    PickRow(R.id.pick7_row, R.id.pick7_label, R.id.pick7_value, R.id.pick7_bar),
    PickRow(R.id.pick8_row, R.id.pick8_label, R.id.pick8_value, R.id.pick8_bar),
    PickRow(R.id.pick9_row, R.id.pick9_label, R.id.pick9_value, R.id.pick9_bar),
    PickRow(R.id.pick10_row, R.id.pick10_label, R.id.pick10_value, R.id.pick10_bar),
)

/** Every prop row a layout holds; a widget shows as many as its height has room for. */
internal val propRows = listOf(
    PropRow(R.id.prop1_row, R.id.prop1_label, R.id.prop1_value),
    PropRow(R.id.prop2_row, R.id.prop2_label, R.id.prop2_value),
    PropRow(R.id.prop3_row, R.id.prop3_label, R.id.prop3_value),
    PropRow(R.id.prop4_row, R.id.prop4_label, R.id.prop4_value),
    PropRow(R.id.prop5_row, R.id.prop5_label, R.id.prop5_value),
    PropRow(R.id.prop6_row, R.id.prop6_label, R.id.prop6_value),
    PropRow(R.id.prop7_row, R.id.prop7_label, R.id.prop7_value),
    PropRow(R.id.prop8_row, R.id.prop8_label, R.id.prop8_value),
    PropRow(R.id.prop9_row, R.id.prop9_label, R.id.prop9_value),
    PropRow(R.id.prop10_row, R.id.prop10_label, R.id.prop10_value),
)

// Heights in dp at the base text sizes, each row's with the gap above it.
private const val HEADER_HEIGHT = 18f
private const val PICK_ROW_HEIGHT = 29f
private const val PROP_ROW_HEIGHT = 20f
private const val PROPS_TITLE_HEIGHT = 22f

/** How much taller than its base height a row is drawn: the widget's [textScale] times the phone's font size setting. */
internal fun textStretch(context: Context, textScale: Float): Float = textScale * context.resources.configuration.fontScale

/** The height in dp a widget of [size] has for rows under its header, inside [padding] dp all round. */
internal fun rowsHeight(size: WidgetSize, padding: Float, stretch: Float): Float =
    size.height - 2 * padding - HEADER_HEIGHT * stretch

internal fun picksThatFit(height: Float, stretch: Float): Int = rowsThatFit(height, PICK_ROW_HEIGHT * stretch, pickRows.size)

internal fun propsThatFit(height: Float, stretch: Float): Int = rowsThatFit(height, PROP_ROW_HEIGHT * stretch, propRows.size)

/** The props that fit in [height] under their own section title. */
internal fun titledPropsThatFit(height: Float, stretch: Float): Int = propsThatFit(height - PROPS_TITLE_HEIGHT * stretch, stretch)

private fun rowsThatFit(height: Float, rowHeight: Float, most: Int): Int = (height / rowHeight).toInt().coerceIn(1, most)

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

/** Fills the first [count] pick rows from [picks]; a row past them or without a pick is hidden. */
internal fun RemoteViews.bindPicks(picks: JSONArray?, count: Int) {
    pickRows.forEachIndexed { index, row ->
        val pick = if (index < count) picks?.optJSONObject(index) else null
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

/**
 * Fills the first [count] prop rows from [props]; a row past them or without a prop is hidden.
 * A tap on a row opens that player's game.
 */
internal fun RemoteViews.bindProps(context: Context, props: JSONArray?, count: Int) {
    val ink = ContextCompat.getColor(context, R.color.widget_ink)
    val sub = ContextCompat.getColor(context, R.color.widget_ink_sub)
    val cyan = ContextCompat.getColor(context, R.color.widget_cyan)
    propRows.forEachIndexed { index, row ->
        val prop = if (index < count) props?.optJSONObject(index) else null
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

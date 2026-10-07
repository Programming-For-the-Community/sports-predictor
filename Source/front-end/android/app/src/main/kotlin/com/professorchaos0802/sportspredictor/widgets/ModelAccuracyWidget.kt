package com.professorchaos0802.sportspredictor.widgets

import android.appwidget.AppWidgetManager
import android.content.Context
import android.content.SharedPreferences
import android.view.View
import android.widget.RemoteViews
import com.professorchaos0802.sportspredictor.R
import org.json.JSONObject

/** One sport's win-probability accuracy: season figure, last period, and how each confidence level did. */
class ModelAccuracyWidget : ResizableWidgetProvider() {
    override fun onUpdate(
        context: Context,
        appWidgetManager: AppWidgetManager,
        appWidgetIds: IntArray,
        widgetData: SharedPreferences,
    ) {
        for (widgetId in appWidgetIds) {
            val views = renderAccuracy(context, widgetData, widgetId, wide = false)
            val size = appWidgetManager.sizeOf(context, widgetId)
            views.scaleText(context, size.textScale(fit), textSizes)
            views.scaleText(context, size.textScale(fullWidthFit), fullWidthTextSizes)
            appWidgetManager.updateAppWidget(widgetId, views)
        }
    }

    private companion object {
        val fit = WidgetSize(115f, 165f)
        val textSizes = mapOf(
            R.dimen.widget_text_figure to listOf(R.id.acc_big),
            R.dimen.widget_text_row to bandViews.map { it.pct },
            R.dimen.widget_text_body to listOf(R.id.acc_last),
            R.dimen.widget_text_tag to bandViews.map { it.tag } + listOf(R.id.acc_bands_title),
        )

        /** The lines that run the widget's full width, so have less room to grow. */
        val fullWidthFit = WidgetSize(150f, 165f)
        val fullWidthTextSizes = mapOf(
            R.dimen.widget_text_body to listOf(R.id.acc_message),
            R.dimen.widget_text_meta to listOf(R.id.acc_season),
            R.dimen.widget_text_caption to listOf(R.id.acc_header),
        )
    }
}

/** The same figures with the score models' average misses alongside. */
class ModelAccuracyWideWidget : ResizableWidgetProvider() {
    override fun onUpdate(
        context: Context,
        appWidgetManager: AppWidgetManager,
        appWidgetIds: IntArray,
        widgetData: SharedPreferences,
    ) {
        for (widgetId in appWidgetIds) {
            val views = renderAccuracy(context, widgetData, widgetId, wide = true)
            views.bindMisses(widgetData.sportFor(widgetId)?.let { widgetData.json("accuracy_$it") })
            val size = appWidgetManager.sizeOf(context, widgetId)
            views.scaleText(context, size.textScale(fit), textSizes)
            views.scaleText(context, size.textScale(missFit), missTextSizes)
            appWidgetManager.updateAppWidget(widgetId, views)
        }
    }

    private companion object {
        val fit = WidgetSize(265f, 150f)
        val textSizes = mapOf(
            R.dimen.widget_text_figure to listOf(R.id.acc_big),
            R.dimen.widget_text_body to listOf(R.id.acc_message, R.id.acc_last),
            R.dimen.widget_text_meta to bandViews.map { it.pct } + listOf(R.id.acc_season),
            R.dimen.widget_text_caption to listOf(R.id.acc_header),
            R.dimen.widget_text_tag to bandViews.map { it.tag } + listOf(R.id.acc_bands_title, R.id.acc_miss_title),
        )

        /** The average-miss rows fill their column's width, so have less room to grow. */
        val missFit = WidgetSize(290f, 150f)
        val missTextSizes = mapOf(R.dimen.widget_text_body to missViews.flatMap { listOf(it.label, it.value) })
    }
}

private class BandViews(val band: Int, val tag: Int, val pct: Int, val bar: Int)

private class MissViews(val row: Int, val label: Int, val value: Int)

private val bandViews = listOf(
    BandViews(R.id.acc_band1, R.id.acc_band1_tag, R.id.acc_band1_pct, R.id.acc_band1_bar),
    BandViews(R.id.acc_band2, R.id.acc_band2_tag, R.id.acc_band2_pct, R.id.acc_band2_bar),
    BandViews(R.id.acc_band3, R.id.acc_band3_tag, R.id.acc_band3_pct, R.id.acc_band3_bar),
)

private val missViews = listOf(
    MissViews(R.id.acc_miss1, R.id.acc_miss1_label, R.id.acc_miss1_value),
    MissViews(R.id.acc_miss2, R.id.acc_miss2_label, R.id.acc_miss2_value),
    MissViews(R.id.acc_miss3, R.id.acc_miss3_label, R.id.acc_miss3_value),
)

/** The parts both accuracy widgets share; their layouts hold the same view ids. */
private fun renderAccuracy(context: Context, widgetData: SharedPreferences, widgetId: Int, wide: Boolean): RemoteViews {
    val views = RemoteViews(context.packageName, if (wide) R.layout.widget_model_accuracy_wide else R.layout.widget_model_accuracy)
    val sport = widgetData.sportFor(widgetId)
    val data = sport?.let { widgetData.json("accuracy_$it") }
    views.openOnTap(context, R.id.widget_root, data?.optString("route")?.takeIf { it.isNotEmpty() })

    val message = when {
        sport == null -> context.getString(R.string.widget_choose_sport)
        data == null -> context.getString(R.string.widget_loading)
        data.has("empty") -> data.getString("empty")
        else -> null
    }
    if (data == null || message != null) {
        views.setTextViewText(R.id.acc_header, data?.optString("sport") ?: context.getString(R.string.widget_accuracy_label))
        views.setTextViewText(R.id.acc_message, message)
        views.setViewVisibility(R.id.acc_message, View.VISIBLE)
        views.setViewVisibility(R.id.acc_body, View.GONE)
        return views
    }
    views.setViewVisibility(R.id.acc_message, View.GONE)
    views.setViewVisibility(R.id.acc_body, View.VISIBLE)
    views.setTextViewText(R.id.acc_header, "${data.getString("sport")} · ${data.getString("model")}")
    views.setTextViewText(R.id.acc_big, percent(data.getDouble("season_pct")))
    val graded = data.getInt("season_n")
    views.setTextViewText(R.id.acc_season, if (wide) "of $graded games" else "right this season · $graded")
    if (data.isNull("last_pct")) {
        views.setViewVisibility(R.id.acc_last, View.GONE)
    } else {
        val label = data.optString("last_label").ifEmpty { "Last" }
        views.setViewVisibility(R.id.acc_last, View.VISIBLE)
        views.setTextViewText(R.id.acc_last, "$label: ${percent(data.getDouble("last_pct"))}")
    }

    // A sport without confidence levels (PGA, F1) has no bands to show.
    val bands = data.optJSONArray("bands")
    views.setViewVisibility(R.id.acc_bands, if (bands != null && bands.length() > 0) View.VISIBLE else View.GONE)
    bandViews.forEachIndexed { index, band ->
        val entry = bands?.optJSONObject(index)
        if (entry == null) {
            views.setViewVisibility(band.band, View.GONE)
            return@forEachIndexed
        }
        views.setViewVisibility(band.band, View.VISIBLE)
        views.setTextViewText(band.tag, entry.getString("tag"))
        // Null while the level has too few graded picks for a percentage.
        val pct = if (entry.isNull("pct")) null else entry.getDouble("pct")
        views.setTextViewText(band.pct, pct?.let { percent(it) } ?: "–")
        views.setProgressBar(band.bar, 100, Math.round((pct ?: 0.0) * 100).toInt(), false)
    }
    return views
}

/** Score margin, home score and away score: each one's average miss this season. */
private fun RemoteViews.bindMisses(data: JSONObject?) {
    val misses = data?.optJSONArray("misses")
    missViews.forEachIndexed { index, views ->
        val miss = misses?.optJSONObject(index)
        if (miss == null) {
            setViewVisibility(views.row, View.GONE)
            return@forEachIndexed
        }
        setViewVisibility(views.row, View.VISIBLE)
        setTextViewText(views.label, miss.getString("label"))
        setTextViewText(views.value, "${miss.getString("value")} ${miss.getString("unit")}")
    }
}

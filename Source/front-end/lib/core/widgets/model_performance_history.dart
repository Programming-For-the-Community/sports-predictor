import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../../static/model_display.dart';
import '../models/model_performance.dart';
import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';
import 'model_performance_format.dart';

const _plotHeight = 96.0;
const _axisWidth = 34.0;
const _barGap = 2.0;
const _swatchSize = 8.0;
// Accuracy never starts above a coin flip; an average miss starts at zero.
const _accuracyFloor = 0.5;
// The amount axis takes the first of these steps that fits in four ticks.
const _amountSteps = [0.1, 0.2, 0.25, 0.5, 1.0, 2.0, 2.5, 5.0, 10.0, 20.0, 25.0, 50.0, 100.0];
const _maxAmountTicks = 4;
const _maxAccuracyTicks = 6;

/// A version's colour on the chart: its palette slot, or versionOlder once it
/// is a full palette or more behind the newest (or unknown).
Color versionColor(int? version, int newest) {
  const palette = AppColors.versionPalette;
  if (version == null || newest - version >= palette.length) return AppColors.versionOlder;
  return palette[(version - 1) % palette.length];
}

/// The newest version the record knows of -- the card's own, or any graded one.
int newestVersion(ModelPerformanceRecord record) => [
      record.version,
      for (final window in [...record.history, ...record.versions]) window.version,
    ].whereType<int>().fold(0, math.max);

/// The chart's y-axis: `lo`..`hi` with a tick at each of `ticks`.
class HistoryAxis {
  const HistoryAxis(this.lo, this.hi, this.ticks);

  final double lo;
  final double hi;
  final List<double> ticks;

  /// Where `value` sits between the bottom (0) and top (1) of the plot.
  double fraction(double value) => ((value - lo) / (hi - lo)).clamp(0.0, 1.0);
}

/// Accuracy runs from 50% (lower if a period fell below it) to the next 10%
/// above the best period; an average miss from 0 to a round step above the
/// worst. Ticks are evenly spaced and few enough to read on a phone.
HistoryAxis historyAxis(List<double> values, {required bool isAmount}) {
  final top = values.isEmpty ? 0.0 : values.reduce(math.max);
  if (isAmount) {
    final step = _amountSteps.firstWhere((s) => top / s <= _maxAmountTicks, orElse: () => _amountSteps.last);
    final count = math.max(1, (top / step - 1e-9).ceil());
    return HistoryAxis(0, count * step, [for (var i = 0; i <= count; i++) _round(i * step)]);
  }
  final bottom = values.isEmpty ? _accuracyFloor : values.reduce(math.min);
  final lo = math.min(_accuracyFloor, (bottom * 10).floor() / 10);
  final hi = math.max(lo + 0.1, math.min(1.0, (top * 10 - 1e-9).ceil() / 10));
  final tenths = ((hi - lo) * 10).round();
  final every = tenths > _maxAccuracyTicks ? 2 : 1;
  return HistoryAxis(lo, hi, [for (var i = 0; i <= tenths; i += every) _round(lo + i / 10)]);
}

double _round(double value) => (value * 1e4).round() / 1e4;

/// A tick's label: "60%" for accuracy, "2.5" for an amount.
String axisTickText(double tick, {required bool isAmount}) {
  if (!isAmount) return '${(tick * 100).round()}%';
  if (tick == tick.roundToDouble()) return '${tick.round()}';
  return tick.toStringAsFixed((tick * 10) == (tick * 10).roundToDouble() ? 1 : 2);
}

/// One legend entry: a version (or every folded "older" one) and its figure.
class VersionLegendEntry {
  const VersionLegendEntry({required this.label, required this.color, required this.value});

  final String label;
  final Color color;
  final double? value;
}

/// Oldest first. Versions a full palette or more behind the newest fold into
/// one "older" entry, whose figure is their prediction-weighted average.
List<VersionLegendEntry> versionLegend(List<PerformanceWindow> versions, int newest) {
  final older = <PerformanceWindow>[];
  final entries = <VersionLegendEntry>[];
  for (final window in versions) {
    final color = versionColor(window.version, newest);
    if (color == AppColors.versionOlder) {
      older.add(window);
    } else {
      entries.add(VersionLegendEntry(label: 'v${window.version}', color: color, value: window.value));
    }
  }
  if (older.isEmpty) return entries;
  return [VersionLegendEntry(label: 'older', color: AppColors.versionOlder, value: _weightedMean(older)), ...entries];
}

double? _weightedMean(List<PerformanceWindow> windows) {
  final graded = windows.where((w) => w.value != null && w.n > 0);
  final n = graded.fold<int>(0, (sum, w) => sum + w.n);
  if (n == 0) return null;
  return graded.fold<double>(0, (sum, w) => sum + w.value! * w.n) / n;
}

/// The season, one bar per finished period, each in the colour of the model
/// version that made it: a y-axis for the scale, a dashed line wherever the
/// version changes, the first and last period under it, and a legend giving
/// each version its own figure. Holding a bar shows its period in full.
class ModelPerformanceHistoryChart extends StatelessWidget {
  const ModelPerformanceHistoryChart({super.key, required this.record, required this.display, required this.isWeekly});

  final ModelPerformanceRecord record;
  final ModelDisplay display;
  final bool isWeekly;

  /// Only a scorecard with at least one finished period has anything to chart.
  static bool shows(ModelPerformanceRecord record) => record.history.isNotEmpty;

  @override
  Widget build(BuildContext context) {
    final history = record.history;
    final newest = newestVersion(record);
    final axis = historyAxis(history.map((p) => p.value).whereType<double>().toList(), isAmount: record.isAmount);
    final axisWidth = MediaQuery.textScalerOf(context).scale(_axisWidth);
    final tickStyle = AppTextStyles.metricValue(color: AppColors.inkMute).copyWith(fontSize: 10);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(historyTitle(record, display, isWeekly: isWeekly), style: AppTextStyles.microLabel()),
        const SizedBox(height: 14),
        SizedBox(
          height: _plotHeight,
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              SizedBox(width: axisWidth, child: _AxisLabels(axis: axis, isAmount: record.isAmount, style: tickStyle)),
              Expanded(child: _Plot(record: record, display: display, axis: axis, newest: newest)),
            ],
          ),
        ),
        const SizedBox(height: 5),
        Padding(
          padding: EdgeInsets.only(left: axisWidth),
          // Each end label gets half the width and wraps -- an event name
          // ("Biltmore Championship") can be wider than a phone allows.
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(child: Text(history.first.label ?? '', style: tickStyle)),
              if (history.length > 1) ...[
                const SizedBox(width: 12),
                Expanded(child: Text(history.last.label ?? '', style: tickStyle, textAlign: TextAlign.right)),
              ],
            ],
          ),
        ),
        const SizedBox(height: 12),
        Wrap(
          spacing: 14,
          runSpacing: 6,
          children: [
            for (final entry in versionLegend(record.versions, newest)) _LegendItem(entry: entry, record: record, display: display),
          ],
        ),
      ],
    );
  }
}

class _AxisLabels extends StatelessWidget {
  const _AxisLabels({required this.axis, required this.isAmount, required this.style});

  final HistoryAxis axis;
  final bool isAmount;
  final TextStyle style;

  @override
  Widget build(BuildContext context) {
    return Stack(
      clipBehavior: Clip.none,
      children: [
        for (final tick in axis.ticks)
          Positioned(
            right: 6,
            bottom: axis.fraction(tick) * _plotHeight - 6,
            child: Text(axisTickText(tick, isAmount: isAmount), style: style),
          ),
      ],
    );
  }
}

class _Plot extends StatelessWidget {
  const _Plot({required this.record, required this.display, required this.axis, required this.newest});

  final ModelPerformanceRecord record;
  final ModelDisplay display;
  final HistoryAxis axis;
  final int newest;

  @override
  Widget build(BuildContext context) {
    final history = record.history;
    return Stack(
      children: [
        for (final (i, tick) in axis.ticks.indexed)
          Positioned(
            left: 0,
            right: 0,
            bottom: axis.fraction(tick) * (_plotHeight - 1),
            child: Container(height: 1, color: i == 0 ? AppColors.borderRaised : AppColors.border),
          ),
        Row(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            for (final (i, period) in history.indexed)
              Expanded(
                child: Padding(
                  padding: EdgeInsets.only(right: i == history.length - 1 ? 0 : _barGap),
                  child: _Bar(
                    period: period,
                    fraction: period.value == null ? 0 : axis.fraction(period.value!),
                    color: versionColor(period.version, newest),
                    versionChanged: i > 0 && history[i - 1].version != period.version,
                    tooltip: _tooltip(period),
                  ),
                ),
              ),
          ],
        ),
      ],
    );
  }

  /// "Wk 7 · 64% · 52 games · v6".
  String _tooltip(PerformanceWindow period) {
    final value = period.value == null ? '--' : periodValueText(record, display, period.value!);
    return [
      if (period.label != null) period.label!,
      record.isAmount ? withUnit(value, display) : value,
      countText(period.n, nounFor(record, display)),
      if (period.version != null) 'v${period.version}',
    ].join(' · ');
  }
}

class _Bar extends StatelessWidget {
  const _Bar({required this.period, required this.fraction, required this.color, required this.versionChanged, required this.tooltip});

  final PerformanceWindow period;
  final double fraction;
  final Color color;
  final bool versionChanged;
  final String tooltip;

  @override
  Widget build(BuildContext context) {
    return Tooltip(
      message: tooltip,
      child: Stack(
        fit: StackFit.expand,
        children: [
          if (versionChanged) const Positioned(left: -1, top: 0, bottom: 0, width: 1, child: CustomPaint(painter: _DashedLinePainter())),
          Align(
            alignment: Alignment.bottomCenter,
            child: FractionallySizedBox(
              heightFactor: fraction,
              widthFactor: 1,
              child: DecoratedBox(
                decoration: BoxDecoration(color: color, borderRadius: const BorderRadius.vertical(top: Radius.circular(2))),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// A vertical dashed line down the full height -- where a new version took over.
class _DashedLinePainter extends CustomPainter {
  const _DashedLinePainter();

  static const _dash = 3.0;
  static const _gap = 3.0;

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = AppColors.inkSub.withValues(alpha: 0.55)
      ..strokeWidth = 1;
    for (var y = 0.0; y < size.height; y += _dash + _gap) {
      canvas.drawLine(Offset(0.5, y), Offset(0.5, math.min(y + _dash, size.height)), paint);
    }
  }

  @override
  bool shouldRepaint(_DashedLinePainter oldDelegate) => false;
}

class _LegendItem extends StatelessWidget {
  const _LegendItem({required this.entry, required this.record, required this.display});

  final VersionLegendEntry entry;
  final ModelPerformanceRecord record;
  final ModelDisplay display;

  @override
  Widget build(BuildContext context) {
    final value = entry.value == null ? '--' : periodValueText(record, display, entry.value!);
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: _swatchSize,
          height: _swatchSize,
          decoration: BoxDecoration(color: entry.color, borderRadius: BorderRadius.circular(2)),
        ),
        const SizedBox(width: 5),
        Text.rich(TextSpan(children: [
          TextSpan(text: '${entry.label} ', style: AppTextStyles.metricValue(color: AppColors.inkSub).copyWith(fontSize: 11, fontWeight: FontWeight.w400)),
          TextSpan(text: value, style: AppTextStyles.metricValue(color: AppColors.ink).copyWith(fontSize: 11)),
        ])),
      ],
    );
  }
}

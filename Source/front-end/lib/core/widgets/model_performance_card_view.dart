import 'package:flutter/material.dart';

import '../../static/model_display.dart';
import '../models/model_performance.dart';
import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';
import 'model_card_frame.dart';
import 'model_performance_format.dart';

// Below this width (scaled by the system text size), a band row stacks its
// tier/count line above its bar instead of putting all four cells on one line.
const _bandRowStackWidth = 400.0;
// Below this width the season and last-period stats stack instead of sitting side by side.
const _headlineStackWidth = 340.0;

/// One model's season and last-period results, in the same card shell as the
/// Models tab's training cards (ModelCardFrame). Nothing here ellipsizes:
/// text wraps and rows re-flow, so a long name, a big number or a large
/// system font never hides part of a result.
class ModelPerformanceCardView extends StatelessWidget {
  const ModelPerformanceCardView({super.key, required this.record, required this.isWeekly, this.seasonLabel = 'THIS SEASON'});

  final ModelPerformanceRecord record;

  /// What the first stat box is called: "THIS SEASON", or "LAST 7 DAYS" for a
  /// sport graded on a rolling window.
  final String seasonLabel;

  /// True for football/basketball ("last week"); false for PGA/F1 ("last event").
  final bool isWeekly;

  @override
  Widget build(BuildContext context) {
    final display = modelDisplay(record.modelName);
    return ModelCardFrame(
      title: modelDisplayName(record.modelName),
      badges: [
        if (record.version != null) 'v${record.version}',
        if (record.lastPeriod?.label != null) record.lastPeriod!.label!,
      ],
      children: [
        const SizedBox(height: 20),
        if (!record.hasResults)
          _EmptyState(noun: nounFor(record, display))
        else ...[
          _Headline(record: record, display: display, isWeekly: isWeekly, seasonLabel: seasonLabel),
          ..._facts(display),
          if (record.bands.isNotEmpty) ...[const SizedBox(height: 20), _Bands(record: record, display: display)],
          if (record.periods.isNotEmpty) ...[const SizedBox(height: 20), _RecentPeriods(record: record, display: display, isWeekly: isWeekly)],
        ],
      ],
    );
  }

  List<Widget> _facts(ModelDisplay display) {
    final baseline = vsBaselineText(record);
    final training = atTrainingText(record, display);
    final lean = seasonLeanText(record, display);
    if (baseline == null && training == null && lean == null) return const [];
    return [
      const SizedBox(height: 16),
      Wrap(
        spacing: 32,
        runSpacing: 12,
        children: [
          if (baseline != null) _Fact(label: 'VS BASELINE', value: baseline, color: AppColors.cyan),
          if (training != null) _Fact(label: 'AT TRAINING', value: training, color: AppColors.inkMid),
          if (lean != null) _Fact(label: 'TENDS TO MISS', value: lean, color: AppColors.inkMid),
        ],
      ),
    ];
  }
}

class _EmptyState extends StatelessWidget {
  const _EmptyState({required this.noun});
  final String noun;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(18),
      decoration: BoxDecoration(borderRadius: BorderRadius.circular(12), border: Border.all(color: AppColors.borderRaised)),
      child: Text('NO GRADED ${noun.toUpperCase()} YET', style: AppTextStyles.microLabel()),
    );
  }
}

class _Fact extends StatelessWidget {
  const _Fact({required this.label, required this.value, required this.color});
  final String label;
  final String value;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        Text(label, style: AppTextStyles.microLabel()),
        const SizedBox(height: 4),
        Text(value, style: AppTextStyles.metricValueLarge(color: color)),
      ],
    );
  }
}

class _Headline extends StatelessWidget {
  const _Headline({required this.record, required this.display, required this.isWeekly, required this.seasonLabel});

  final ModelPerformanceRecord record;
  final ModelDisplay display;
  final bool isWeekly;
  final String seasonLabel;

  @override
  Widget build(BuildContext context) {
    final label = headlineLabel(record);
    final season = _StatBox(
      heading: '$seasonLabel · $label',
      value: record.season.value,
      record: record,
      display: display,
      sample: countText(record.season.n, nounFor(record, display)),
    );
    final last = record.lastPeriod;
    final lastBox = last == null
        ? null
        : _StatBox(
            heading: '${isWeekly ? 'LAST WEEK' : 'LAST EVENT'} · $label',
            value: last.value,
            record: record,
            display: display,
            sample: countText(last.n, nounFor(record, display)),
            delta: describeDelta(record, display),
          );

    return LayoutBuilder(
      builder: (context, constraints) {
        if (lastBox == null || constraints.maxWidth < _headlineStackWidth * MediaQuery.textScalerOf(context).scale(1)) {
          return Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [season, if (lastBox != null) ...[const SizedBox(height: 12), lastBox]],
          );
        }
        return IntrinsicHeight(
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [Expanded(child: season), const SizedBox(width: 12), Expanded(child: lastBox)],
          ),
        );
      },
    );
  }
}

class _StatBox extends StatelessWidget {
  const _StatBox({
    required this.heading,
    required this.value,
    required this.record,
    required this.display,
    required this.sample,
    this.delta,
  });

  final String heading;
  final double? value;
  final ModelPerformanceRecord record;
  final ModelDisplay display;
  final String sample;
  final DeltaText? delta;

  @override
  Widget build(BuildContext context) {
    final number = value == null ? '--' : headlineNumber(record, display, value!);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
      decoration: BoxDecoration(
        color: AppColors.inset,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: AppColors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(heading, style: AppTextStyles.microLabel()),
          const SizedBox(height: 6),
          Text.rich(TextSpan(children: [
            TextSpan(text: number, style: AppTextStyles.metricValueLarge(color: AppColors.cyan).copyWith(fontSize: 26)),
            if (record.isAmount && display.valueUnit.isNotEmpty)
              TextSpan(text: ' ${display.valueUnit}', style: AppTextStyles.body(color: AppColors.inkSub).copyWith(fontSize: 13)),
          ])),
          const SizedBox(height: 4),
          Text(sample, style: AppTextStyles.body(color: AppColors.inkSub).copyWith(fontSize: 12.5)),
          if (delta != null) ...[
            const SizedBox(height: 4),
            Text(_deltaGlyph(delta!) + delta!.text, style: AppTextStyles.metricValue(color: _toneColor(delta!.tone)).copyWith(fontSize: 12)),
          ],
        ],
      ),
    );
  }

  static String _deltaGlyph(DeltaText delta) => switch (delta.tone) {
        DeltaTone.good => '▲ ',
        DeltaTone.bad => '▼ ',
        DeltaTone.flat => '■ ',
      };

  static Color _toneColor(DeltaTone tone) => switch (tone) {
        DeltaTone.good => AppColors.pos,
        DeltaTone.bad => AppColors.neg,
        DeltaTone.flat => AppColors.inkSub,
      };
}

class _Bands extends StatelessWidget {
  const _Bands({required this.record, required this.display});

  final ModelPerformanceRecord record;
  final ModelDisplay display;

  @override
  Widget build(BuildContext context) {
    final captionStyle = AppTextStyles.body(color: AppColors.inkSub).copyWith(fontSize: 12, height: 1.5);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text.rich(TextSpan(children: [
          TextSpan(text: '${bandTitle(record)}  ', style: AppTextStyles.microLabel()),
          TextSpan(text: bandCaption(record, display), style: captionStyle),
        ])),
        const SizedBox(height: 4),
        Text.rich(TextSpan(children: [
          TextSpan(text: 'BAR  ', style: AppTextStyles.microLabel()),
          TextSpan(text: barCaption(record, display), style: captionStyle),
        ])),
        const SizedBox(height: 10),
        for (var i = 0; i < record.bands.length; i++) ...[
          if (i > 0) const Divider(height: 1, color: AppColors.border),
          _BandRow(band: record.bands[i], record: record, display: display),
        ],
      ],
    );
  }
}

class _BandRow extends StatelessWidget {
  const _BandRow({required this.band, required this.record, required this.display});

  final PerformanceBand band;
  final ModelPerformanceRecord record;
  final ModelDisplay display;

  @override
  Widget build(BuildContext context) {
    final tier = _Tier(tag: band.tag, range: bandRangeText(band, record, display));
    final count = Text(countText(band.n, nounFor(record, display)), style: AppTextStyles.body(color: AppColors.inkSub).copyWith(fontSize: 12.5));
    final pct = band.pct;
    final lean = band.early ? null : leanText(band.bias, record, display);

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 10),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _row(context, tier, count, pct),
          if (lean != null) ...[
            const SizedBox(height: 6),
            Text(lean, style: AppTextStyles.body(color: AppColors.inkSub).copyWith(fontSize: 12.5)),
          ],
        ],
      ),
    );
  }

  Widget _row(BuildContext context, Widget tier, Widget count, double? pct) {
    return LayoutBuilder(
        builder: (context, constraints) {
          final stacked = constraints.maxWidth < _bandRowStackWidth * MediaQuery.textScalerOf(context).scale(1);
          if (band.early || pct == null) {
            return Wrap(
              spacing: 12,
              runSpacing: 6,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [tier, count, Text('Too early', style: AppTextStyles.body(color: AppColors.inkMute).copyWith(fontSize: 13))],
            );
          }
          final bar = _Bar(fraction: pct);
          final value = Text('${(pct * 100).round()}%', style: AppTextStyles.metricValue(color: AppColors.ink));
          if (stacked) {
            return Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [Flexible(child: tier), const SizedBox(width: 12), Flexible(child: Align(alignment: Alignment.topRight, child: count))],
                ),
                const SizedBox(height: 8),
                Row(children: [Expanded(child: bar), const SizedBox(width: 12), value]),
              ],
            );
          }
          return Row(
            crossAxisAlignment: CrossAxisAlignment.center,
            children: [
              SizedBox(width: 120, child: tier),
              const SizedBox(width: 12),
              SizedBox(width: 96, child: count),
              const SizedBox(width: 12),
              Expanded(child: bar),
              const SizedBox(width: 12),
              value,
            ],
          );
        },
    );
  }
}

class _Tier extends StatelessWidget {
  const _Tier({required this.tag, required this.range});

  final String tag;
  final String? range;

  @override
  Widget build(BuildContext context) {
    final (foreground, background) = switch (tag) {
      'HIGH' => (AppColors.cyan, AppColors.cyan.withValues(alpha: 0.12)),
      'MED' => (AppColors.warn, AppColors.warn.withValues(alpha: 0.12)),
      _ => (AppColors.inkSub, AppColors.inset),
    };
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
          decoration: BoxDecoration(color: background, borderRadius: BorderRadius.circular(999)),
          child: Text(tag, style: AppTextStyles.microLabel(color: foreground)),
        ),
        if (range != null) ...[
          const SizedBox(height: 4),
          Text(range!, style: AppTextStyles.metricValue(color: AppColors.inkSub).copyWith(fontSize: 11.5)),
        ],
      ],
    );
  }
}

class _Bar extends StatelessWidget {
  const _Bar({required this.fraction});
  final double fraction;

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 9,
      decoration: BoxDecoration(color: AppColors.inset, borderRadius: BorderRadius.circular(4)),
      alignment: Alignment.centerLeft,
      child: FractionallySizedBox(
        widthFactor: fraction.clamp(0.0, 1.0),
        child: Container(decoration: BoxDecoration(color: AppColors.cyan, borderRadius: BorderRadius.circular(4))),
      ),
    );
  }
}

class _RecentPeriods extends StatelessWidget {
  const _RecentPeriods({required this.record, required this.display, required this.isWeekly});

  final ModelPerformanceRecord record;
  final ModelDisplay display;
  final bool isWeekly;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(isWeekly ? 'RECENT WEEKS' : 'RECENT EVENTS', style: AppTextStyles.microLabel()),
        const SizedBox(height: 10),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            for (var i = 0; i < record.periods.length; i++)
              _PeriodChip(period: record.periods[i], record: record, display: display, isLast: i == record.periods.length - 1),
          ],
        ),
      ],
    );
  }
}

class _PeriodChip extends StatelessWidget {
  const _PeriodChip({required this.period, required this.record, required this.display, required this.isLast});

  final PerformanceWindow period;
  final ModelPerformanceRecord record;
  final ModelDisplay display;
  final bool isLast;

  @override
  Widget build(BuildContext context) {
    final value = period.value == null ? '--' : periodValueText(record, display, period.value!);
    final color = isLast ? AppColors.cyan : AppColors.inkSub;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: isLast ? AppColors.cyan.withValues(alpha: 0.10) : AppColors.inset,
        borderRadius: BorderRadius.circular(8),
      ),
      child: Text.rich(TextSpan(children: [
        TextSpan(text: '${period.label ?? ''}  ', style: AppTextStyles.metricValue(color: color).copyWith(fontSize: 12.5, fontWeight: FontWeight.w400)),
        TextSpan(text: value, style: AppTextStyles.metricValue(color: isLast ? AppColors.cyan : AppColors.ink).copyWith(fontSize: 12.5)),
      ])),
    );
  }
}

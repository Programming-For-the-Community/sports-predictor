import 'package:flutter/material.dart';

import '../../static/model_display.dart';
import '../../static/nfl_team_colors.dart';
import '../models/model_performance.dart';
import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';
import 'model_performance_format.dart';

// Below this width (scaled by the system text size) each entry's value sits
// under its name instead of beside it.
const _leaderInlineWidth = 340.0;

/// "Most accurate on": the teams or players a model has done best with this
/// season -- the leader highlighted, the rest listed under it.
class ModelPerformanceBest extends StatelessWidget {
  const ModelPerformanceBest({super.key, required this.sport, required this.record, required this.display, required this.isWeekly});

  final String sport;
  final ModelPerformanceRecord record;
  final ModelDisplay display;
  final bool isWeekly;

  @override
  Widget build(BuildContext context) {
    final ranking = record.best!;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text('MOST ACCURATE ON', style: AppTextStyles.microLabel()),
        const SizedBox(height: 10),
        if (ranking.entities.isEmpty)
          Text('Not enough graded ${isWeekly ? 'games' : 'events'} yet', style: AppTextStyles.body(color: AppColors.inkSub).copyWith(fontSize: 13))
        else ...[
          _Leader(widget: this, entity: ranking.entities.first, isTeam: ranking.entityType == 'team'),
          for (var i = 1; i < ranking.entities.length; i++) _RankRow(widget: this, rank: i + 1, entity: ranking.entities[i]),
        ],
      ],
    );
  }
}

/// "92%" for accuracy, "2.3 pts" for a miss.
String _valueText(ModelPerformanceBest widget, BestEntity entity) {
  if (!widget.record.isAmount) return percent(entity.value, decimals: 0);
  return withUnit(entity.value.toStringAsFixed(widget.display.missDecimals), widget.display);
}

/// "4 / 4" for accuracy, "avg miss" for a miss.
String _valueCaption(ModelPerformanceBest widget, BestEntity entity) {
  if (widget.record.isAmount) return 'avg miss';
  return '${(entity.value * entity.n).round()} / ${entity.n}';
}

String _sampleText(ModelPerformanceBest widget, int n) => countText(n, widget.isWeekly ? 'games' : 'events');

String _nameOf(BestEntity entity) => entity.name ?? entity.abbreviation ?? entity.entityId;

class _Leader extends StatelessWidget {
  const _Leader({required this.widget, required this.entity, required this.isTeam});

  final ModelPerformanceBest widget;
  final BestEntity entity;
  final bool isTeam;

  @override
  Widget build(BuildContext context) {
    final team = teamDisplayFor(widget.sport, entity.entityId, entity.abbreviation, apiColor: entity.color);
    final markText = isTeam ? team.abbreviation : _initials(_nameOf(entity));
    final mark = Container(
      width: 40,
      height: 40,
      alignment: Alignment.center,
      decoration: BoxDecoration(color: team.primary ?? AppColors.surface, borderRadius: BorderRadius.circular(10)),
      child: FittedBox(
        child: Padding(
          padding: const EdgeInsets.all(4),
          child: Text(markText, style: AppTextStyles.microLabel(color: AppColors.ink).copyWith(letterSpacing: 0.5)),
        ),
      ),
    );
    final name = Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(_nameOf(entity), style: AppTextStyles.body(color: AppColors.ink).copyWith(fontSize: 16, fontWeight: FontWeight.w600)),
        Text(_sampleText(widget, entity.n), style: AppTextStyles.body(color: AppColors.inkSub).copyWith(fontSize: 12.5)),
      ],
    );
    final value = Text(_valueText(widget, entity), style: AppTextStyles.metricValueLarge(color: AppColors.cyan));
    final caption = Text(_valueCaption(widget, entity), style: AppTextStyles.body(color: AppColors.inkSub).copyWith(fontSize: 12));

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      decoration: BoxDecoration(
        color: AppColors.inset,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: AppColors.borderRaised),
      ),
      child: LayoutBuilder(
        builder: (context, constraints) {
          if (constraints.maxWidth < _leaderInlineWidth * MediaQuery.textScalerOf(context).scale(1)) {
            return Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(children: [mark, const SizedBox(width: 12), Expanded(child: name)]),
                const SizedBox(height: 10),
                Wrap(
                  spacing: 8,
                  crossAxisAlignment: WrapCrossAlignment.end,
                  children: [value, Padding(padding: const EdgeInsets.only(bottom: 3), child: caption)],
                ),
              ],
            );
          }
          return Row(
            children: [
              mark,
              const SizedBox(width: 12),
              Expanded(child: name),
              const SizedBox(width: 12),
              Column(crossAxisAlignment: CrossAxisAlignment.end, children: [value, caption]),
            ],
          );
        },
      ),
    );
  }

  static String _initials(String name) {
    final parts = name.split(RegExp(r'\s+')).where((p) => p.isNotEmpty).toList();
    if (parts.isEmpty) return '?';
    return (parts.first[0] + (parts.length > 1 ? parts.last[0] : '')).toUpperCase();
  }
}

class _RankRow extends StatelessWidget {
  const _RankRow({required this.widget, required this.rank, required this.entity});

  final ModelPerformanceBest widget;
  final int rank;
  final BestEntity entity;

  @override
  Widget build(BuildContext context) {
    final name = Text(_nameOf(entity), style: AppTextStyles.body(color: AppColors.inkMid).copyWith(fontSize: 14));
    final value = Text.rich(TextSpan(children: [
      TextSpan(text: _valueText(widget, entity), style: AppTextStyles.metricValue(color: AppColors.ink).copyWith(fontSize: 13)),
      TextSpan(
        text: '  ${_sampleText(widget, entity.n)}',
        style: AppTextStyles.body(color: AppColors.inkSub).copyWith(fontSize: 12),
      ),
    ]));
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 2, vertical: 9),
      decoration: const BoxDecoration(border: Border(bottom: BorderSide(color: AppColors.border))),
      child: LayoutBuilder(
        builder: (context, constraints) {
          final stacked = constraints.maxWidth < _leaderInlineWidth * MediaQuery.textScalerOf(context).scale(1);
          return Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              SizedBox(width: 22, child: Text('$rank', style: AppTextStyles.metricValue(color: AppColors.inkMute).copyWith(fontSize: 12))),
              if (stacked)
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [name, const SizedBox(height: 2), value],
                  ),
                )
              else ...[
                Expanded(child: name),
                const SizedBox(width: 12),
                value,
              ],
            ],
          );
        },
      ),
    );
  }
}

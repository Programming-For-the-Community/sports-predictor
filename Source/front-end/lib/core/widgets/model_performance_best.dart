import 'package:flutter/material.dart';

import '../../static/model_display.dart';
import '../../static/nfl_team_colors.dart';
import '../models/model_performance.dart';
import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';
import 'model_performance_format.dart';
import 'status_toggle.dart';

/// "Most accurate on": the teams or players a model has done best with this
/// season -- the leader highlighted, the rest listed under it. A player prop
/// can switch between miss as a share of actual and the raw miss.
class ModelPerformanceBest extends StatefulWidget {
  const ModelPerformanceBest({super.key, required this.sport, required this.record, required this.display, required this.isWeekly});

  final String sport;
  final ModelPerformanceRecord record;
  final ModelDisplay display;
  final bool isWeekly;

  @override
  State<ModelPerformanceBest> createState() => _ModelPerformanceBestState();
}

class _ModelPerformanceBestState extends State<ModelPerformanceBest> {
  bool _relative = true;

  @override
  Widget build(BuildContext context) {
    final relative = widget.record.bestRelative;
    final showRelative = relative != null && _relative;
    final ranking = showRelative ? relative : widget.record.best!;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Wrap(
          spacing: 8,
          runSpacing: 8,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            Text('MOST ACCURATE ON', style: AppTextStyles.microLabel()),
            if (relative != null) ...[
              StatusToggle(label: '% OF ACTUAL', selected: _relative, onTap: () => setState(() => _relative = true), accentColor: AppColors.cyan),
              StatusToggle(
                label: 'RAW ${widget.display.valueUnit.toUpperCase()}'.trim(),
                selected: !_relative,
                onTap: () => setState(() => _relative = false),
                accentColor: AppColors.cyan,
              ),
            ],
          ],
        ),
        const SizedBox(height: 10),
        if (ranking.entities.isEmpty)
          Text('Not enough graded ${widget.isWeekly ? 'games' : 'events'} yet', style: AppTextStyles.body(color: AppColors.inkSub).copyWith(fontSize: 13))
        else ...[
          _Leader(widget: widget, entity: ranking.entities.first, isTeam: ranking.entityType == 'team', relative: showRelative),
          for (var i = 1; i < ranking.entities.length; i++)
            _RankRow(widget: widget, rank: i + 1, entity: ranking.entities[i], relative: showRelative),
        ],
      ],
    );
  }
}

/// "92%" for accuracy, "2.3 pts" for a miss, "6%" for a share of actual.
String _valueText(ModelPerformanceBest widget, BestEntity entity, bool relative) {
  if (relative || !widget.record.isAmount) return percent(entity.value, decimals: 0);
  return withUnit(entity.value.toStringAsFixed(widget.display.missDecimals), widget.display);
}

/// "4 / 4" for accuracy, "avg miss" or "of actual" for a miss.
String _valueCaption(ModelPerformanceBest widget, BestEntity entity, bool relative) {
  if (relative) return 'of actual';
  if (widget.record.isAmount) return 'avg miss';
  return '${(entity.value * entity.n).round()} / ${entity.n}';
}

String _sampleText(ModelPerformanceBest widget, int n) => countText(n, widget.isWeekly ? 'games' : 'events');

String _nameOf(BestEntity entity) => entity.name ?? entity.abbreviation ?? entity.entityId;

class _Leader extends StatelessWidget {
  const _Leader({required this.widget, required this.entity, required this.isTeam, required this.relative});

  final ModelPerformanceBest widget;
  final BestEntity entity;
  final bool isTeam;
  final bool relative;

  @override
  Widget build(BuildContext context) {
    final team = teamDisplayFor(widget.sport, entity.entityId, entity.abbreviation, apiColor: entity.color);
    final markText = isTeam ? team.abbreviation : _initials(_nameOf(entity));
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      decoration: BoxDecoration(
        color: AppColors.inset,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: AppColors.borderRaised),
      ),
      child: Row(
        children: [
          Container(
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
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(_nameOf(entity), style: AppTextStyles.body(color: AppColors.ink).copyWith(fontSize: 16, fontWeight: FontWeight.w600)),
                Text(_sampleText(widget, entity.n), style: AppTextStyles.body(color: AppColors.inkSub).copyWith(fontSize: 12.5)),
              ],
            ),
          ),
          const SizedBox(width: 12),
          Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Text(_valueText(widget, entity, relative), style: AppTextStyles.metricValueLarge(color: AppColors.cyan)),
              Text(_valueCaption(widget, entity, relative), style: AppTextStyles.body(color: AppColors.inkSub).copyWith(fontSize: 12)),
            ],
          ),
        ],
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
  const _RankRow({required this.widget, required this.rank, required this.entity, required this.relative});

  final ModelPerformanceBest widget;
  final int rank;
  final BestEntity entity;
  final bool relative;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 2, vertical: 9),
      decoration: const BoxDecoration(border: Border(bottom: BorderSide(color: AppColors.border))),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(width: 22, child: Text('$rank', style: AppTextStyles.metricValue(color: AppColors.inkMute).copyWith(fontSize: 12))),
          Expanded(child: Text(_nameOf(entity), style: AppTextStyles.body(color: AppColors.inkMid).copyWith(fontSize: 14))),
          const SizedBox(width: 12),
          Text.rich(TextSpan(children: [
            TextSpan(text: _valueText(widget, entity, relative), style: AppTextStyles.metricValue(color: AppColors.ink).copyWith(fontSize: 13)),
            TextSpan(
              text: '  ${_sampleText(widget, entity.n)}',
              style: AppTextStyles.body(color: AppColors.inkSub).copyWith(fontSize: 12),
            ),
          ])),
        ],
      ),
    );
  }
}

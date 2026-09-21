import 'package:flutter/material.dart';

import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';

/// Formats a points total -- whole numbers once >= 1000 (season-long
/// running totals), one decimal place below that.
String formatPoints(double value) => value >= 1000 ? value.round().toString() : value.toStringAsFixed(1);

String formatPercent(double value) => '${(value * 100).round()}%';

class PercentText extends StatelessWidget {
  const PercentText(this.value, {super.key});
  final double value;

  @override
  Widget build(BuildContext context) {
    return Text(
      formatPercent(value), style: AppTextStyles.metricValue(color: AppColors.violet), textAlign: TextAlign.center,
      maxLines: 1, softWrap: false, overflow: TextOverflow.ellipsis,
    );
  }
}

class StandingsHeaderRow extends StatelessWidget {
  const StandingsHeaderRow({super.key, required this.labels, required this.flexes});
  final List<String> labels;
  final List<int> flexes;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        for (var i = 0; i < labels.length; i++) ...[
          if (i > 0) const SizedBox(width: 6),
          Expanded(
            flex: flexes[i],
            child: Text(
              labels[i], style: AppTextStyles.microLabel(),
              textAlign: i == 0 ? TextAlign.center : (i == 1 ? TextAlign.start : TextAlign.center),
              maxLines: 1, softWrap: false, overflow: TextOverflow.ellipsis,
            ),
          ),
        ],
      ],
    );
  }
}

/// A season-points standings table: rank / name / points (current +
/// projected) / championship probability -- F1's own Drivers' and
/// Constructors' Championship tables share this exact shape, differing
/// only in the entity type and the name column's own header label.
class PointsStandingsTable<T> extends StatelessWidget {
  const PointsStandingsTable({
    super.key,
    required this.standings,
    required this.nameColumnLabel,
    required this.displayName,
    required this.currentPoints,
    required this.projectedPoints,
    required this.championProbability,
  });

  final List<T> standings;
  final String nameColumnLabel;
  final String Function(T) displayName;
  final double Function(T) currentPoints;
  final double Function(T) projectedPoints;
  final double Function(T) championProbability;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 20),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(16),
        gradient: const LinearGradient(begin: Alignment.topLeft, end: Alignment.bottomRight, colors: AppColors.surfaceGrad),
        border: Border.all(color: AppColors.borderRaised),
      ),
      child: Column(
        children: [
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 10),
            child: StandingsHeaderRow(labels: ['#', nameColumnLabel, 'POINTS', 'CHAMP%'], flexes: const [1, 4, 2, 2]),
          ),
          for (var i = 0; i < standings.length; i++) ...[
            const Divider(height: 1, color: AppColors.border),
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 12),
              child: Row(
                children: [
                  Expanded(flex: 1, child: Text('${i + 1}', style: AppTextStyles.metricValue(color: AppColors.inkMute), textAlign: TextAlign.center)),
                  const SizedBox(width: 6),
                  Expanded(
                    flex: 4,
                    child: Text(
                      displayName(standings[i]), style: AppTextStyles.body(color: AppColors.ink),
                      maxLines: 1, overflow: TextOverflow.ellipsis,
                    ),
                  ),
                  const SizedBox(width: 6),
                  Expanded(
                    flex: 2,
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Text(formatPoints(currentPoints(standings[i])), style: AppTextStyles.metricValue(color: AppColors.ink), maxLines: 1),
                        Text(formatPoints(projectedPoints(standings[i])), style: AppTextStyles.microLabel(color: AppColors.cyan), maxLines: 1),
                      ],
                    ),
                  ),
                  const SizedBox(width: 6),
                  Expanded(
                    flex: 2,
                    child: Text(
                      formatPercent(championProbability(standings[i])), style: AppTextStyles.metricValue(color: AppColors.violet),
                      textAlign: TextAlign.center, maxLines: 1, softWrap: false, overflow: TextOverflow.ellipsis,
                    ),
                  ),
                ],
              ),
            ),
          ],
        ],
      ),
    );
  }
}

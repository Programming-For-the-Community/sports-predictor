import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../data/events_repository.dart';
import '../models/event.dart';
import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';
import '../../static/nfl_team_colors.dart';
import 'confidence_pill.dart';
import 'win_probability_bar.dart';

// Abbreviated for this compact row -- the backend's full round names
// ("Conference Championship") don't fit the fixed-width week/round slot
// this list uses. See core/models/event.dart's SportEvent.round.
const _roundAbbreviations = {
  'Wild Card': 'WC',
  'Divisional': 'DIV',
  'Conference Championship': 'CONF',
  'Super Bowl': 'SB',
};

String _weekLabel(SportEvent event) {
  final round = event.round;
  if (round != null) return _roundAbbreviations[round] ?? round;
  return event.week != null ? 'WK ${event.week}' : '';
}

/// design/FRONTEND_STYLE.md's "Game row (list)" component.
///
/// Scheduled events fetch their own live prediction (one request per
/// visible row) rather than the list page batching them -- NFL's weekly
/// game count (~16) makes this a non-issue, and it means a slow/failed
/// prediction for one game never blocks the rest of the list from
/// rendering. Completed events do NOT fetch a live prediction -- they use
/// `event.predictionComparison`, the prediction actually logged before
/// the game was played (see handler.py's _prediction_comparison). A fresh
/// live prediction for an already-played game would be misleading (built
/// from rolling stats that may already include this game's own now-
/// normalized result) and wouldn't answer "how did the model do", which
/// is the whole point of the completed tab.
class GameRow extends ConsumerWidget {
  const GameRow({super.key, required this.sport, required this.event});

  final String sport;
  final SportEvent event;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final home = nflTeam(event.home.entityId);
    final away = nflTeam(event.away.entityId);
    final isCompleted = event.status == 'completed';

    return InkWell(
      onTap: () => context.go('/$sport/events/${event.eventId}'),
      borderRadius: BorderRadius.circular(16),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 16),
        decoration: BoxDecoration(
          color: AppColors.surface,
          borderRadius: BorderRadius.circular(16),
          border: Border.all(color: AppColors.border),
        ),
        child: Row(
          children: [
            SizedBox(
              width: 56,
              child: Text(
                _weekLabel(event),
                style: AppTextStyles.microLabel(),
              ),
            ),
            Expanded(
              flex: 2,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  _TeamLine(color: home.primary, abbr: home.abbreviation, role: 'H', score: event.home.result?.score),
                  const SizedBox(height: 4),
                  _TeamLine(color: away.primary, abbr: away.abbreviation, role: 'A', score: event.away.result?.score),
                ],
              ),
            ),
            const SizedBox(width: 16),
            Expanded(
              flex: 3,
              child: isCompleted
                  ? _ComparisonSummary(comparison: event.predictionComparison)
                  : _LivePrediction(sport: sport, eventId: event.eventId),
            ),
          ],
        ),
      ),
    );
  }
}

class _LivePrediction extends ConsumerWidget {
  const _LivePrediction({required this.sport, required this.eventId});
  final String sport;
  final String eventId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final prediction = ref.watch(eventPredictionProvider((sport: sport, eventId: eventId)));
    return prediction.when(
      data: (p) => Row(
        children: [
          Expanded(child: WinProbabilityBar(homeWinProbability: p.homeWinProbability)),
          const SizedBox(width: 12),
          Text(
            '${(p.homeWinProbability * 100).round()}%',
            style: AppTextStyles.metricValueLarge(color: AppColors.cyan),
          ),
          const SizedBox(width: 8),
          ConfidencePill(homeWinProbability: p.homeWinProbability),
        ],
      ),
      loading: () => const WinProbabilityBar(homeWinProbability: 0.5),
      error: (_, __) => Text('--', style: AppTextStyles.body(color: AppColors.inkMute)),
    );
  }
}

class _ComparisonSummary extends StatelessWidget {
  const _ComparisonSummary({required this.comparison});
  final PredictionComparison? comparison;

  @override
  Widget build(BuildContext context) {
    final c = comparison;
    if (c == null) {
      return Text('No prediction recorded', style: AppTextStyles.body(color: AppColors.inkMute));
    }
    return Row(
      children: [
        Icon(
          c.correct ? Icons.check_circle : Icons.cancel,
          color: c.correct ? AppColors.live : AppColors.neg,
          size: 18,
        ),
        const SizedBox(width: 8),
        Expanded(
          child: Text(
            'Predicted ${(c.predictedHomeWinProbability * 100).round()}% home win'
            '${c.predictedMargin != null ? ' -- margin ${c.predictedMargin!.toStringAsFixed(1)}' : ''}',
            style: AppTextStyles.body(color: AppColors.inkSub),
            overflow: TextOverflow.ellipsis,
          ),
        ),
      ],
    );
  }
}

class _TeamLine extends StatelessWidget {
  const _TeamLine({required this.color, required this.abbr, required this.role, this.score});
  final Color color;
  final String abbr;
  final String role; // 'H' or 'A' -- kept to a single letter, this row is compact
  final double? score;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(width: 8, height: 8, decoration: BoxDecoration(shape: BoxShape.circle, color: color)),
        const SizedBox(width: 8),
        Text(abbr, style: AppTextStyles.body(color: AppColors.ink)),
        const SizedBox(width: 4),
        Text(role, style: AppTextStyles.microLabel(color: AppColors.inkMute)),
        if (score != null) ...[
          const SizedBox(width: 8),
          Text(score!.toStringAsFixed(0), style: AppTextStyles.metricValue(color: AppColors.ink)),
        ],
      ],
    );
  }
}

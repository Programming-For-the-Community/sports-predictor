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

/// "1:00 PM" in the viewer's own local time -- '' if kickoffTime is
/// absent (an event ingested before that field existed).
String _kickoffTimeLabel(SportEvent event) {
  final kickoff = event.kickoffTime;
  if (kickoff == null) return '';
  final local = DateTime.tryParse(kickoff)?.toLocal();
  if (local == null) return '';
  final hour12 = local.hour % 12 == 0 ? 12 : local.hour % 12;
  final minute = local.minute.toString().padLeft(2, '0');
  final period = local.hour < 12 ? 'AM' : 'PM';
  return '$hour12:$minute $period';
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
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(_weekLabel(event), style: AppTextStyles.microLabel()),
                  if (_kickoffTimeLabel(event).isNotEmpty)
                    Text(_kickoffTimeLabel(event), style: AppTextStyles.microLabel(color: AppColors.inkMute)),
                ],
              ),
            ),
            Expanded(
              flex: 2,
              child: _MatchupLine(
                awayColor: away.primary, awayAbbr: away.abbreviation, awayScore: event.away.result?.score,
                homeColor: home.primary, homeAbbr: home.abbreviation, homeScore: event.home.result?.score,
              ),
            ),
            const SizedBox(width: 16),
            Expanded(
              flex: 3,
              child: isCompleted
                  ? _ComparisonSummary(
                      comparison: event.predictionComparison, homeAbbr: home.abbreviation, awayAbbr: away.abbreviation,
                    )
                  : _LivePrediction(
                      sport: sport, eventId: event.eventId, homeAbbr: home.abbreviation, awayAbbr: away.abbreviation,
                    ),
            ),
          ],
        ),
      ),
    );
  }
}

class _LivePrediction extends ConsumerWidget {
  const _LivePrediction({required this.sport, required this.eventId, required this.homeAbbr, required this.awayAbbr});
  final String sport;
  final String eventId;
  final String homeAbbr;
  final String awayAbbr;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final prediction = ref.watch(eventPredictionProvider((sport: sport, eventId: eventId)));
    return prediction.when(
      data: (p) {
        final pickAbbr = p.homeWinProbability >= 0.5 ? homeAbbr : awayAbbr;
        return Row(
          children: [
            Expanded(child: WinProbabilityBar(homeWinProbability: p.homeWinProbability)),
            const SizedBox(width: 12),
            Column(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                Text(
                  '${(p.homeWinProbability * 100).round()}%',
                  style: AppTextStyles.metricValueLarge(color: AppColors.cyan),
                ),
                // Predicted winner + margin + score, up front on the list
                // card rather than only after clicking into the event
                // (MatchupHero's own PICK/PRED MARGIN/PRED TOTAL trio).
                Text(
                  '$pickAbbr -${p.margin.abs().toStringAsFixed(1)} '
                  '(${p.homeScore.round()}-${p.awayScore.round()})',
                  style: AppTextStyles.microLabel(color: AppColors.inkMute),
                ),
              ],
            ),
            const SizedBox(width: 8),
            ConfidencePill(homeWinProbability: p.homeWinProbability),
          ],
        );
      },
      loading: () => const WinProbabilityBar(homeWinProbability: 0.5),
      error: (_, __) => Text('--', style: AppTextStyles.body(color: AppColors.inkMute)),
    );
  }
}

class _ComparisonSummary extends StatelessWidget {
  const _ComparisonSummary({required this.comparison, required this.homeAbbr, required this.awayAbbr});
  final PredictionComparison? comparison;
  final String homeAbbr;
  final String awayAbbr;

  @override
  Widget build(BuildContext context) {
    final c = comparison;
    if (c == null) {
      return Text('No prediction recorded', style: AppTextStyles.body(color: AppColors.inkMute));
    }
    final pickAbbr = c.predictedHomeWon ? homeAbbr : awayAbbr;
    final predictedScore = c.predictedHomeScore != null && c.predictedAwayScore != null
        ? ' (${c.predictedHomeScore!.round()}-${c.predictedAwayScore!.round()})'
        : '';
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
            'Predicted $pickAbbr'
            '${c.predictedMargin != null ? ' by ${c.predictedMargin!.abs().toStringAsFixed(1)}' : ''}'
            '$predictedScore',
            style: AppTextStyles.body(color: AppColors.inkSub),
            overflow: TextOverflow.ellipsis,
          ),
        ),
      ],
    );
  }
}

/// Single "away @ home" line -- "@" is the standard American-sports
/// shorthand for "traveling to" (the away team is always on the left),
/// so this ordering is the only one "@" reads correctly for.
class _MatchupLine extends StatelessWidget {
  const _MatchupLine({
    required this.awayColor, required this.awayAbbr, this.awayScore,
    required this.homeColor, required this.homeAbbr, this.homeScore,
  });
  final Color awayColor;
  final String awayAbbr;
  final double? awayScore;
  final Color homeColor;
  final String homeAbbr;
  final double? homeScore;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(width: 8, height: 8, decoration: BoxDecoration(shape: BoxShape.circle, color: awayColor)),
        const SizedBox(width: 8),
        Text(awayAbbr, style: AppTextStyles.body(color: AppColors.ink)),
        if (awayScore != null) ...[
          const SizedBox(width: 6),
          Text(awayScore!.toStringAsFixed(0), style: AppTextStyles.metricValue(color: AppColors.ink)),
        ],
        const SizedBox(width: 8),
        Text('@', style: AppTextStyles.microLabel(color: AppColors.inkMute)),
        const SizedBox(width: 8),
        Container(width: 8, height: 8, decoration: BoxDecoration(shape: BoxShape.circle, color: homeColor)),
        const SizedBox(width: 8),
        Text(homeAbbr, style: AppTextStyles.body(color: AppColors.ink)),
        if (homeScore != null) ...[
          const SizedBox(width: 6),
          Text(homeScore!.toStringAsFixed(0), style: AppTextStyles.metricValue(color: AppColors.ink)),
        ],
      ],
    );
  }
}

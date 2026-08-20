import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../data/events_repository.dart';
import '../models/event.dart';
import '../models/live_score.dart';
import '../models/prediction.dart';
import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';
import '../../static/nfl_team_colors.dart';
import 'confidence_pill.dart';
import 'live_status_pill.dart';
import 'prediction_computing_retry.dart';
import 'prediction_freshness_badge.dart';
import 'team_color_dot.dart';
import 'win_probability_bar.dart';

// Below this width, GameRow stacks the matchup and prediction sections
// onto their own lines instead of splitting one row between them.
const _stackBreakpoint = 600.0;

// Abbreviated for this compact row -- the backend's full round names
// don't fit the fixed-width week/round slot this list uses.
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

const _usTimeZones = {
  -5: ('EST', 'EDT'),
  -6: ('CST', 'CDT'),
  -7: ('MST', 'MDT'),
  -8: ('PST', 'PDT'),
  -9: ('AKST', 'AKDT'),
  -10: ('HST', 'HST'),
};

/// The viewer's own timezone, abbreviated (e.g. "EDT"), stated once for
/// the whole list rather than per row. Not DateTime.timeZoneName
/// (unreliable on Flutter Web); instead derives the standard (non-DST)
/// offset by comparing January/July, then checks whether the current
/// offset differs from it to pick DST vs standard. Falls back to
/// "UTC±N" outside the continental US/AK/HI.
String localTimezoneLabel() {
  final now = DateTime.now();
  final janOffset = DateTime(now.year, 1, 15).timeZoneOffset;
  final julOffset = DateTime(now.year, 7, 15).timeZoneOffset;
  final standardOffset = janOffset <= julOffset ? janOffset : julOffset;
  final isDst = now.timeZoneOffset != standardOffset;

  final names = _usTimeZones[standardOffset.inHours];
  if (names != null) return isDst ? names.$2 : names.$1;

  final offset = now.timeZoneOffset;
  final sign = offset.isNegative ? '-' : '+';
  return 'UTC$sign${offset.abs().inHours}';
}

/// design/FRONTEND_STYLE.md's "Game row (list)" component.
///
/// Scheduled events fetch their own live prediction (one request per
/// visible row) rather than the list page batching them, so a slow/failed
/// prediction for one game never blocks the rest of the list. Completed
/// events use `event.predictionComparison`, the prediction actually
/// logged before the game was played, instead of a fresh live prediction
/// -- that answers "how did the model do" for the completed tab.
class GameRow extends ConsumerWidget {
  const GameRow({super.key, required this.sport, required this.event, this.liveState});

  final String sport;
  final SportEvent event;
  // From liveScoresProvider. Null for the vast majority of rows (anything
  // not within 15 minutes of its own kickoff), not an error or a loading
  // state.
  final LiveEventState? liveState;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final home = teamDisplay(sport, event.home);
    final away = teamDisplay(sport, event.away);
    final isCompleted = event.status == 'completed';
    final isLive = liveState?.live ?? false;
    // Only fetched for a not-yet-started, not-live event -- watched here
    // (not inside _LivePredictionSummary) because _MatchupLine below needs
    // this same resolved value too, for the predicted score shown next to
    // each team's own actual score.
    final prediction = (!isLive && !isCompleted)
        ? ref.watch(eventPredictionProvider((sport: sport, eventId: event.eventId)))
        : null;
    final predicted = prediction?.when(data: (p) => p, loading: () => null, error: (_, __) => null);
    // A completed event has no live prediction; the prediction logged
    // before kickoff is already on hand via predictionComparison, the same
    // source _ComparisonSummary below uses.
    final comparison = event.predictionComparison;
    final awayPredictedScore = predicted?.awayScore ?? (isCompleted ? comparison?.predictedAwayScore : null);
    final homePredictedScore = predicted?.homeScore ?? (isCompleted ? comparison?.predictedHomeScore : null);

    final weekTime = SizedBox(
      // Wide enough for "1:00 PM" on one line.
      width: 72,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(_weekLabel(event), style: AppTextStyles.microLabel()),
          if (_kickoffTimeLabel(event).isNotEmpty)
            Text(
              _kickoffTimeLabel(event),
              style: AppTextStyles.microLabel(color: AppColors.inkMute),
              maxLines: 1,
              softWrap: false,
            ),
        ],
      ),
    );
    final matchup = _MatchupLine(
      awayColor: away.primary, awayAbbr: away.abbreviation,
      awayScore: isLive ? liveState!.awayScore : event.away.result?.score,
      awayPredictedScore: awayPredictedScore,
      homeColor: home.primary, homeAbbr: home.abbreviation,
      homeScore: isLive ? liveState!.homeScore : event.home.result?.score,
      homePredictedScore: homePredictedScore,
    );
    // Stadium name/city/state -- renders nothing when absent rather than
    // a blank line.
    final venueLine = _VenueLine(label: event.venueLabel);
    // Live takes priority over completed/scheduled -- a pre-game
    // prediction next to an actual in-progress score would be confusing.
    final predictionArea = isLive
        ? _LiveStatus(detail: liveState!.detail)
        : isCompleted
            ? _ComparisonSummary(
                comparison: comparison, homeAbbr: home.abbreviation, awayAbbr: away.abbreviation,
              )
            : _LivePredictionSummary(
                prediction: prediction!, homeAbbr: home.abbreviation, awayAbbr: away.abbreviation,
                sport: sport, eventId: event.eventId,
              );

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
        child: LayoutBuilder(
          builder: (context, constraints) {
            // Two team dots/names/scores, plus a percentage/pill/margin
            // line, need more width than a phone-size card has to split
            // between them on one shared row. Each section gets the
            // card's full width on its own line instead, below
            // _stackBreakpoint.
            if (constraints.maxWidth < _stackBreakpoint) {
              return Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(children: [weekTime, const SizedBox(width: 16), Expanded(child: matchup)]),
                  if (event.venueLabel != null) ...[const SizedBox(height: 8), venueLine],
                  const SizedBox(height: 12),
                  predictionArea,
                ],
              );
            }
            return Row(
              children: [
                weekTime,
                Expanded(flex: 2, child: matchup),
                const SizedBox(width: 16),
                Expanded(flex: 2, child: venueLine),
                const SizedBox(width: 16),
                Expanded(flex: 3, child: predictionArea),
              ],
            );
          },
        ),
      ),
    );
  }
}

/// Right-hand side of a scheduled event's row: win-probability bar, the
/// favored team's own percentage above the pick/margin line, and a
/// confidence pill. Takes the already-resolved AsyncValue rather than
/// watching the provider itself, since GameRow.build hands the same value
/// to _MatchupLine too.
class _LivePredictionSummary extends StatelessWidget {
  const _LivePredictionSummary({
    required this.prediction, required this.homeAbbr, required this.awayAbbr,
    required this.sport, required this.eventId,
  });
  final AsyncValue<EventPrediction> prediction;
  final String homeAbbr;
  final String awayAbbr;
  // Only needed to retry on a cold-cache-miss (see the error branch below).
  final String sport;
  final String eventId;

  @override
  Widget build(BuildContext context) {
    return prediction.when(
      data: (p) {
        final homeFavored = p.homeWinProbability >= 0.5;
        final pickAbbr = homeFavored ? homeAbbr : awayAbbr;
        // The favored team's own probability, not always home's, so this
        // matches the pick/margin printed right below it.
        final pickWinProbability = homeFavored ? p.homeWinProbability : 1 - p.homeWinProbability;
        return Row(
          children: [
            Expanded(child: WinProbabilityBar(homeWinProbability: p.homeWinProbability)),
            const SizedBox(width: 12),
            // Flexible+ellipsis on both lines so the column gives up its
            // own width before pushing ConfidencePill past the card edge.
            Flexible(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Text(
                    '${(pickWinProbability * 100).round()}%',
                    style: AppTextStyles.metricValueLarge(color: AppColors.cyan),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                  // Predicted winner + margin -- the score itself renders
                  // next to each team's own actual score on the left
                  // (_MatchupLine).
                  Text(
                    '$pickAbbr -${p.margin.abs().toStringAsFixed(1)}',
                    style: AppTextStyles.microLabel(color: AppColors.inkMute),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                  PredictionFreshnessBadge(
                    sport: sport, eventId: eventId,
                    stale: p.stale, retryAfterSeconds: p.staleRetryAfterSeconds, compact: true,
                  ),
                ],
              ),
            ),
            const SizedBox(width: 8),
            ConfidencePill(homeWinProbability: p.homeWinProbability),
          ],
        );
      },
      loading: () => const WinProbabilityBar(homeWinProbability: 0.5),
      error: (error, _) => error is PredictionComputingException
          ? PredictionComputingRetry(sport: sport, eventId: eventId, retryAfterSeconds: error.retryAfterSeconds, compact: true)
          : Text('--', style: AppTextStyles.body(color: AppColors.inkMute)),
    );
  }
}

/// The pulsing-dot "LIVE" pill plus ESPN's own game-clock text (e.g. "Q3
/// 08:14"), replacing the pre-game prediction/completed-comparison slot
/// while an event is in progress.
class _LiveStatus extends StatelessWidget {
  const _LiveStatus({required this.detail});
  final String? detail;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        const LiveStatusPill(),
        if (detail != null) ...[
          const SizedBox(width: 8),
          Expanded(
            child: Text(detail!, style: AppTextStyles.body(color: AppColors.inkSub), overflow: TextOverflow.ellipsis),
          ),
        ],
      ],
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
    // The predicted winner's own probability, not always home's, matching
    // the pick/margin/score orientation in the text below.
    final pickWinProbability = c.predictedHomeWon ? c.predictedHomeWinProbability : 1 - c.predictedHomeWinProbability;
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
            // The score itself renders next to each team's own actual
            // score on the left (_MatchupLine); this states only the
            // pick/margin/probability.
            'Predicted $pickAbbr (${(pickWinProbability * 100).round()}%)'
            '${c.predictedMargin != null ? ' by ${c.predictedMargin!.abs().toStringAsFixed(1)}' : ''}',
            style: AppTextStyles.body(color: AppColors.inkSub),
            overflow: TextOverflow.ellipsis,
          ),
        ),
      ],
    );
  }
}

/// Away line stacked over home line -- this block only gets a fixed flex
/// slice of GameRow's own Row, not enough room for two team dots + names
/// + scores side by side on one line. Stacking gives each team's own line
/// the full width. Away-then-home (not a home/away label) is the only
/// marker of which side is which, matching the "away @ home" convention.
class _MatchupLine extends StatelessWidget {
  const _MatchupLine({
    required this.awayColor, required this.awayAbbr, this.awayScore, this.awayPredictedScore,
    required this.homeColor, required this.homeAbbr, this.homeScore, this.homePredictedScore,
  });
  final Color? awayColor;
  final String awayAbbr;
  final double? awayScore;
  final double? awayPredictedScore;
  final Color? homeColor;
  final String homeAbbr;
  final double? homeScore;
  final double? homePredictedScore;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        _TeamLine(color: awayColor, abbr: awayAbbr, score: awayScore, predictedScore: awayPredictedScore),
        const SizedBox(height: 4),
        _TeamLine(color: homeColor, abbr: homeAbbr, score: homeScore, predictedScore: homePredictedScore),
      ],
    );
  }
}

class _TeamLine extends StatelessWidget {
  const _TeamLine({required this.color, required this.abbr, this.score, this.predictedScore});
  final Color? color;
  final String abbr;
  final double? score;
  // The prediction logged for this team (live pre-game, or the one
  // recorded before kickoff for a completed game) -- shown in parens
  // right next to the actual score for easy comparison.
  final double? predictedScore;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        TeamColorDot(color: color),
        const SizedBox(width: 6),
        Flexible(child: Text(abbr, style: AppTextStyles.body(color: AppColors.ink), maxLines: 1, overflow: TextOverflow.ellipsis)),
        if (score != null) ...[
          const SizedBox(width: 6),
          Flexible(
            child: Text(
              score!.toStringAsFixed(0),
              style: AppTextStyles.metricValue(color: AppColors.ink),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
          ),
        ],
        if (predictedScore != null) ...[
          const SizedBox(width: 4),
          Flexible(
            child: Text(
              '(${predictedScore!.round()})',
              style: AppTextStyles.microLabel(color: AppColors.inkMute),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
          ),
        ],
      ],
    );
  }
}

/// Stadium name + city/state -- renders nothing when SportEvent.venueLabel
/// is null. Location/name only, excludes venue_indoor.
class _VenueLine extends StatelessWidget {
  const _VenueLine({required this.label});
  final String? label;

  @override
  Widget build(BuildContext context) {
    final text = label;
    if (text == null) return const SizedBox.shrink();
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(Icons.location_on_outlined, size: 12, color: AppColors.inkMute),
        const SizedBox(width: 4),
        Flexible(
          child: Text(
            text,
            style: AppTextStyles.microLabel(color: AppColors.inkMute),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
        ),
      ],
    );
  }
}

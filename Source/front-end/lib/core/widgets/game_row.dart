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
// onto their own lines instead of splitting one row between them -- see
// GameRow.build's own comment.
const _stackBreakpoint = 600.0;

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

const _usTimeZones = {
  -5: ('EST', 'EDT'),
  -6: ('CST', 'CDT'),
  -7: ('MST', 'MDT'),
  -8: ('PST', 'PDT'),
  -9: ('AKST', 'AKDT'),
  -10: ('HST', 'HST'),
};

/// The viewer's own timezone, abbreviated (e.g. "EDT") -- every kickoff
/// time on this list is shown in this same local time (_kickoffTimeLabel
/// above), stated once for the whole list rather than per row. Not
/// DateTime.timeZoneName (unreliable on Flutter Web); instead derives the
/// standard (non-DST) offset by comparing January/July, then checks
/// whether the current offset differs from it to pick DST vs standard.
/// Falls back to "UTC±N" outside the continental US/AK/HI.
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
  const GameRow({super.key, required this.sport, required this.event, this.liveState});

  final String sport;
  final SportEvent event;
  // From liveScoresProvider (core/data/live_scores_repository.dart) --
  // null for the vast majority of rows (anything not within 15 minutes
  // of its own kickoff, per live_scores.py's own POLL_START_BEFORE_KICKOFF),
  // not an error or a loading state.
  final LiveEventState? liveState;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final home = teamDisplay(sport, event.home);
    final away = teamDisplay(sport, event.away);
    final isCompleted = event.status == 'completed';
    final isLive = liveState?.live ?? false;
    // Only fetched for a not-yet-started, not-live event -- watched here
    // (not inside _LivePredictionSummary, which used to watch it itself)
    // because _MatchupLine below needs this SAME resolved value too, for
    // the predicted score now shown next to each team's own actual score,
    // not a second independent fetch of it.
    final prediction = (!isLive && !isCompleted)
        ? ref.watch(eventPredictionProvider((sport: sport, eventId: event.eventId)))
        : null;
    final predicted = prediction?.when(data: (p) => p, loading: () => null, error: (_, __) => null);
    // A completed event has no live prediction (see above), but the
    // prediction actually logged before kickoff is already on hand via
    // predictionComparison -- same source _ComparisonSummary below uses,
    // so the left-hand predicted score and the right-hand pick/margin
    // always describe the same recorded prediction.
    final comparison = event.predictionComparison;
    final awayPredictedScore = predicted?.awayScore ?? (isCompleted ? comparison?.predictedAwayScore : null);
    final homePredictedScore = predicted?.homeScore ?? (isCompleted ? comparison?.predictedHomeScore : null);

    final weekTime = SizedBox(
      // Wide enough for "1:00 PM" on one line -- 56 was sized for the
      // week label ("WK 2"/"DIV") above it, not this longer string,
      // which was wrapping onto a second line as a result. maxLines/
      // softWrap are a backstop, not the fix itself -- a time value
      // truncating would be worse than it wrapping.
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
    // Stadium name/city/state -- absent on any event ingested before this
    // field existed, or a neutral-site game ESPN didn't report an address
    // for, in which case this renders nothing rather than a blank line.
    final venueLine = _VenueLine(label: event.venueLabel);
    // Live takes priority over completed/scheduled -- a pre-game
    // prediction next to an actual in-progress score would be confusing,
    // and a "completed" status hasn't caught up yet (see event.dart's
    // own status field -- it only ever reflects yesterday's batch
    // ingest, never today's game in progress).
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
            // line, together need more width than a phone-size card has
            // to split between them on one shared row -- confirmed live:
            // team names rendered at an effectively invisible sliver of
            // a width. Each section gets the card's full width on its
            // own line instead, below _stackBreakpoint.
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
/// favored team's own percentage (above the pick/margin line -- the
/// percentage and the pick it belongs to read top-to-bottom as one unit),
/// and a confidence pill. Takes the already-resolved AsyncValue rather
/// than watching the provider itself -- GameRow.build watches it once and
/// hands the same value here AND to _MatchupLine (for the predicted score
/// shown next to each team's own actual score), instead of two
/// independent fetches of the same prediction.
class _LivePredictionSummary extends StatelessWidget {
  const _LivePredictionSummary({
    required this.prediction, required this.homeAbbr, required this.awayAbbr,
    required this.sport, required this.eventId,
  });
  final AsyncValue<EventPrediction> prediction;
  final String homeAbbr;
  final String awayAbbr;
  // Only needed to retry on a cold-cache-miss (see the error branch below)
  // -- prediction itself is already resolved by the caller.
  final String sport;
  final String eventId;

  @override
  Widget build(BuildContext context) {
    return prediction.when(
      data: (p) {
        final homeFavored = p.homeWinProbability >= 0.5;
        final pickAbbr = homeFavored ? homeAbbr : awayAbbr;
        // The favored team's own probability, not always home's -- so
        // this number always matches the pick/margin printed right below
        // it instead of flipping to "35%" whenever the away team is
        // favored.
        final pickWinProbability = homeFavored ? p.homeWinProbability : 1 - p.homeWinProbability;
        return Row(
          children: [
            Expanded(child: WinProbabilityBar(homeWinProbability: p.homeWinProbability)),
            const SizedBox(width: 12),
            // Flexible+ellipsis on both lines -- on a narrow phone this
            // column was pushing the ConfidencePill past the card's own
            // edge instead of giving up its own width first.
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
                  // Predicted winner + margin -- the score half of this
                  // used to live here too, but now renders next to each
                  // team's own actual score on the left (_MatchupLine),
                  // so repeating it here would just be the same two
                  // numbers a second time.
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
/// 08:14") -- replaces the pre-game prediction/completed-comparison slot
/// while an event is actually in progress (see GameRow.build).
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
    // The predicted winner's own probability, not always home's -- same
    // orientation as the pick/margin/score in the text below, so this
    // always matches rather than reading like a different team's number
    // whenever the away team was the pick.
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
            // The score half of "predicted X by Y (score)" used to live
            // here too -- it now renders next to each team's own actual
            // score on the left (_MatchupLine), so this only states the
            // pick/margin/probability, not the same numbers twice.
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

/// Away line stacked over home line -- this whole block only gets a
/// fixed flex slice of GameRow's own Row (see GameRow.build), which
/// measured out to as little as ~74px total on a real phone width: not
/// enough room for two team dots + names + scores side by side on one
/// line at all (confirmed -- team names were rendering at an
/// effectively-invisible sliver of a width). Stacking gives each team's
/// own line the full width instead of splitting it with the other
/// team's, which is what actually fixes it, not just squeezing further.
/// Away-then-home (not a home/away label) is the only marker of which
/// side is which -- same "away is listed first" convention American
/// sports use in "away @ home" shorthand, just without printing the "@"
/// itself (that used to prefix the home line, but blended into the
/// score/percentage/margin already competing for room on a narrow card).
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
  // right next to the actual score so the two are easy to compare at a
  // glance, rather than needing to cross-reference the pick/margin text
  // on the other side of the card.
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
          // Flexible, not a bare Text -- a score is normally only 1-2
          // digits, but this line's own fixed overhead (dot, spacers) is
          // already close enough to this card's available width on a
          // real phone that a non-flexible score risked a hard overflow
          // on its own, independent of how much room the name got.
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

/// Stadium name + city/state -- absent entirely (renders nothing) when
/// SportEvent.venueLabel has nothing to show, same "don't render if
/// null" rule the rest of this card follows for optional fields.
/// Deliberately excludes venue_indoor -- location/name only.
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

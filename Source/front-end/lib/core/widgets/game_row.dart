import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../data/events_repository.dart';
import '../models/event.dart';
import '../models/live_score.dart';
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

/// The viewer's own UTC offset, formatted like "UTC-5" -- every kickoff
/// time on this list is shown in this same local time (_kickoffTimeLabel
/// above), so event_list_page.dart states it once for the whole list
/// rather than repeating it on every row. Not DateTime.timeZoneName
/// ("EST"/"CDT") -- Flutter Web frequently can't produce a real
/// abbreviation there (browser/ICU-data dependent); a numeric UTC offset
/// works identically on every platform this app runs on.
String localTimezoneLabel() {
  final offset = DateTime.now().timeZoneOffset;
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
    final home = nflTeam(event.home.entityId);
    final away = nflTeam(event.away.entityId);
    final isCompleted = event.status == 'completed';
    final isLive = liveState?.live ?? false;

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
              // Wide enough for "1:00 PM" on one line -- 56 was sized for
              // the week label ("WK 2"/"DIV") above it, not this longer
              // string, which was wrapping onto a second line as a
              // result. maxLines/softWrap are a backstop, not the fix
              // itself -- a time value truncating would be worse than it
              // wrapping.
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
            ),
            Expanded(
              flex: 2,
              child: _MatchupLine(
                awayColor: away.primary, awayAbbr: away.abbreviation,
                awayScore: isLive ? liveState!.awayScore : event.away.result?.score,
                homeColor: home.primary, homeAbbr: home.abbreviation,
                homeScore: isLive ? liveState!.homeScore : event.home.result?.score,
              ),
            ),
            const SizedBox(width: 16),
            Expanded(
              flex: 3,
              // Live takes priority over completed/scheduled -- a pre-game
              // prediction next to an actual in-progress score would be
              // confusing, and a "completed" status hasn't caught up yet
              // (see event.dart's own status field -- it only ever
              // reflects yesterday's batch ingest, never today's game in
              // progress).
              child: isLive
                  ? _LiveStatus(detail: liveState!.detail)
                  : isCompleted
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
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
          decoration: BoxDecoration(color: AppColors.live.withValues(alpha: 0.15), borderRadius: BorderRadius.circular(999)),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Container(width: 6, height: 6, decoration: BoxDecoration(shape: BoxShape.circle, color: AppColors.live)),
              const SizedBox(width: 6),
              Text('LIVE', style: AppTextStyles.microLabel(color: AppColors.live)),
            ],
          ),
        ),
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
      children: [
        Container(width: 8, height: 8, decoration: BoxDecoration(shape: BoxShape.circle, color: awayColor)),
        const SizedBox(width: 8),
        // Flexible+ellipsis, not a bare Text -- this whole line is
        // squeezed into a fixed flex slice of GameRow's own Row (see
        // GameRow.build), tight enough on a phone-width screen that even
        // a normal 2-3 letter abbreviation needs somewhere to give
        // ground rather than overflow (nflTeam's own fallback for an
        // unrecognized team_id is worse still -- the raw, much longer
        // entity_id string).
        Flexible(child: Text(awayAbbr, style: AppTextStyles.body(color: AppColors.ink), overflow: TextOverflow.ellipsis)),
        if (awayScore != null) ...[
          const SizedBox(width: 6),
          // Flexible here too -- a real (not just placeholder-null) score
          // is now common content on this line once an event goes live,
          // not something that was ever exercised at this width before.
          Flexible(
            child: Text(
              awayScore!.toStringAsFixed(0),
              style: AppTextStyles.metricValue(color: AppColors.ink),
              overflow: TextOverflow.ellipsis,
            ),
          ),
        ],
        const SizedBox(width: 8),
        Text('@', style: AppTextStyles.microLabel(color: AppColors.inkMute)),
        const SizedBox(width: 8),
        Container(width: 8, height: 8, decoration: BoxDecoration(shape: BoxShape.circle, color: homeColor)),
        const SizedBox(width: 8),
        Flexible(child: Text(homeAbbr, style: AppTextStyles.body(color: AppColors.ink), overflow: TextOverflow.ellipsis)),
        if (homeScore != null) ...[
          const SizedBox(width: 6),
          Flexible(
            child: Text(
              homeScore!.toStringAsFixed(0),
              style: AppTextStyles.metricValue(color: AppColors.ink),
              overflow: TextOverflow.ellipsis,
            ),
          ),
        ],
      ],
    );
  }
}

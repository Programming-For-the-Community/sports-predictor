import 'package:flutter/material.dart';

import '../../core/models/field_live_score.dart';
import '../../core/models/field_prediction.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/field_status_pill.dart';

/// PGA's own leaderboard table -- column-spec pattern, same shape as
/// season_page.dart's own _StandingsTable/_StandingsColumn (chosen over
/// the simpler _LeaderboardCard pattern there: a ~150-golfer field needs
/// 6+ compact columns, which is exactly what _StandingsColumn's
/// List<{label, flex, cell}> shape was built for). Public (not private
/// like _StandingsTable) since it's used from field_event_detail_page.dart,
/// a different file.
class _LeaderboardColumn {
  const _LeaderboardColumn(this.label, this.flex, this.cell);
  final String label;
  final int flex;
  // rowNumber is the row's own 1-based position in the currently
  // displayed (sorted) list -- used by the '#' column as a fallback so
  // every row always shows SOME number, not '--', even before any real
  // standing exists.
  final Widget Function(BuildContext context, FieldParticipantPrediction entry, FieldParticipantLiveResult? live, int rowNumber) cell;
}

/// The single round a golfer's own top-level row summarizes: whichever
/// round is currently being projected (applicable_rounds always returns
/// at most one -- aws-lambdas/pga/predict/live_features.py), or, once
/// nothing is left to project, the most recently completed real round.
int? _currentRoundNumber(FieldParticipantPrediction entry) {
  if (entry.rounds.isNotEmpty) return entry.rounds.keys.first;
  if (entry.actualRounds.isNotEmpty) return entry.actualRounds.keys.reduce((a, b) => a > b ? a : b);
  return null;
}

List<_LeaderboardColumn> _leaderboardColumns() => [
      _LeaderboardColumn('#', 1, (context, entry, live, rowNumber) {
        final position = live?.finishPosition ?? entry.actualFinishPosition;
        final isTie = live?.isTie ?? false;
        // Falls back to the row's own displayed position rather than
        // '--' -- every row gets a real number, whether or not a real
        // tournament standing exists yet.
        final label = position != null ? (isTie ? 'T$position' : '$position') : '$rowNumber';
        return Text(
          label, style: AppTextStyles.metricValue(color: AppColors.inkMute), textAlign: TextAlign.center,
          maxLines: 1, softWrap: false, overflow: TextOverflow.ellipsis,
        );
      }),
      _LeaderboardColumn('PLAYER', 4, (context, entry, live, rowNumber) {
        final name = entry.name ?? entry.entityId;
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(name, style: AppTextStyles.body(color: AppColors.ink), maxLines: 1, overflow: TextOverflow.ellipsis),
            if (entry.country != null)
              Text(entry.country!, style: AppTextStyles.microLabel(color: AppColors.inkSub)),
          ],
        );
      }),
      // The status pill the user asked for -- one per golfer row, showing
      // their round status (scheduled/finished/cut/MDF/withdrawn/still
      // playing). Prefers the live overlay's status when present (fresher),
      // falls back to the prediction response's own actual-result absence
      // (no live overlay fetched yet, or this event isn't in a live window).
      _LeaderboardColumn('STATUS', 2, (context, entry, live, rowNumber) {
        final status = live?.status ?? (entry.actualFinishPosition != null ? 'finished' : null);
        return Center(child: FieldStatusPill(status: status));
      }),
      _LeaderboardColumn('TO PAR', 2, (context, entry, live, rowNumber) {
        final scoreToPar = live?.scoreToPar ?? entry.actualScoreToPar;
        return Text(
          _formatToPar(scoreToPar), style: AppTextStyles.metricValue(), textAlign: TextAlign.center,
        );
      }),
      // The current round's own proj/actual, visible without expanding
      // the row -- the full 1-4 breakdown is still only in the expanded
      // panel below (_RoundBreakdownStrip), but the round most relevant
      // right now doesn't require a tap to see.
      _LeaderboardColumn('THIS RD', 2, (context, entry, live, rowNumber) {
        final round = _currentRoundNumber(entry);
        if (round == null) {
          return Text('--', style: AppTextStyles.metricValue(color: AppColors.inkMute), textAlign: TextAlign.center);
        }
        return Center(
          child: _RoundCell(round: round, projected: entry.rounds[round]?.value, actual: _actualForRound(round, entry, live)),
        );
      }),
      _LeaderboardColumn('PROJ', 2, (context, entry, live, rowNumber) {
        final projected = entry.projectedScoreToPar?.value;
        return Text(
          projected != null ? _formatToPar(projected) : '--',
          style: AppTextStyles.metricValue(color: AppColors.cyan), textAlign: TextAlign.center,
        );
      }),
      _LeaderboardColumn('TOP 10%', 2, (context, entry, live, rowNumber) => _PercentText(entry.top10Probability?.value)),
      _LeaderboardColumn('TOP 5%', 2, (context, entry, live, rowNumber) => _PercentText(entry.top5Probability?.value)),
    ];

String _formatToPar(num? scoreToPar) {
  if (scoreToPar == null) return '--';
  final rounded = scoreToPar.round();
  if (rounded == 0) return 'E';
  return rounded > 0 ? '+$rounded' : '$rounded';
}

// live overlay first (freshest during an active poll window), falling
// back to the prediction response's own real (not gated on the whole
// tournament being "completed") current standing -- same precedence the
// TO PAR column already uses.
double? _standingScoreToPar(FieldParticipantPrediction entry, FieldParticipantLiveResult? live) {
  final liveScore = live?.scoreToPar;
  if (liveScore != null) return liveScore.toDouble();
  return entry.actualScoreToPar;
}

/// Real current standing first (ascending to-par -- lower is better),
/// falling back to the server's own projected order ONLY for golfers with
/// no real standing at all yet (pre-tournament, or simply haven't teed
/// off this round) -- per the user's explicit request. A stable sort:
/// ties within either group preserve the original (server) order rather
/// than reshuffling arbitrarily.
List<FieldParticipantPrediction> _sortedByStanding(List<FieldParticipantPrediction> field, Map<String, FieldParticipantLiveResult> liveResults) {
  final indexed = [for (var i = 0; i < field.length; i++) (index: i, entry: field[i])];
  indexed.sort((a, b) {
    final aStanding = _standingScoreToPar(a.entry, liveResults[a.entry.entityId]);
    final bStanding = _standingScoreToPar(b.entry, liveResults[b.entry.entityId]);
    if (aStanding != null && bStanding != null) {
      final cmp = aStanding.compareTo(bStanding);
      return cmp != 0 ? cmp : a.index.compareTo(b.index);
    }
    if (aStanding != null) return -1; // has a real standing -> ranks above one that doesn't
    if (bStanding != null) return 1;
    return a.index.compareTo(b.index); // neither has a standing -- preserve projected order
  });
  return [for (final e in indexed) e.entry];
}

class FieldLeaderboardTable extends StatelessWidget {
  const FieldLeaderboardTable({super.key, required this.field, this.liveResults = const {}});

  final List<FieldParticipantPrediction> field;
  // Optional overlay from fieldLiveScoresProvider -- the table itself
  // doesn't own polling, the detail page feeds fresher data in as it
  // arrives (same split GameRow uses for its own isLive/liveState params).
  final Map<String, FieldParticipantLiveResult> liveResults;

  @override
  Widget build(BuildContext context) {
    if (field.isEmpty) {
      return Text('No field available yet.', style: AppTextStyles.body(color: AppColors.inkSub));
    }
    final columns = _leaderboardColumns();
    final sorted = _sortedByStanding(field, liveResults);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 20),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(16),
        gradient: LinearGradient(begin: Alignment.topLeft, end: Alignment.bottomRight, colors: AppColors.surfaceGrad),
        border: Border.all(color: AppColors.borderRaised),
      ),
      child: Column(
        children: [
          Padding(padding: const EdgeInsets.symmetric(vertical: 10), child: _LeaderboardHeaderRow(columns: columns)),
          for (var i = 0; i < sorted.length; i++) ...[
            const Divider(height: 1, color: AppColors.border),
            _LeaderboardRow(entry: sorted[i], live: liveResults[sorted[i].entityId], columns: columns, rowNumber: i + 1),
          ],
        ],
      ),
    );
  }
}

class _LeaderboardHeaderRow extends StatelessWidget {
  const _LeaderboardHeaderRow({required this.columns});

  final List<_LeaderboardColumn> columns;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        // Leading space matching the expand-chevron column below, so
        // header labels line up with their own cell, not the chevron.
        const SizedBox(width: 20),
        for (var i = 0; i < columns.length; i++) ...[
          if (i > 0) const SizedBox(width: 6),
          Expanded(
            flex: columns[i].flex,
            child: Text(
              columns[i].label,
              style: AppTextStyles.microLabel(),
              textAlign: i == 0 ? TextAlign.start : TextAlign.center,
              maxLines: 1,
              softWrap: false,
              overflow: TextOverflow.ellipsis,
            ),
          ),
        ],
      ],
    );
  }
}

/// Tap a row to reveal its own full ROUND 1-4 proj-vs-actual strip
/// (_RoundBreakdownStrip) below the standard columns -- the current
/// round's own summary is already visible at the top level (the 'THIS RD'
/// column), the expanded view is for the FULL history, always all 4
/// rounds regardless of how many have real data yet.
class _LeaderboardRow extends StatefulWidget {
  const _LeaderboardRow({required this.entry, required this.live, required this.columns, required this.rowNumber});

  final FieldParticipantPrediction entry;
  final FieldParticipantLiveResult? live;
  final List<_LeaderboardColumn> columns;
  final int rowNumber;

  @override
  State<_LeaderboardRow> createState() => _LeaderboardRowState();
}

class _LeaderboardRowState extends State<_LeaderboardRow> {
  bool _expanded = false;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: () => setState(() => _expanded = !_expanded),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.center,
              children: [
                Icon(
                  _expanded ? Icons.expand_less : Icons.expand_more,
                  size: 18, color: AppColors.inkMute,
                ),
                const SizedBox(width: 2),
                for (final column in widget.columns)
                  Expanded(flex: column.flex, child: column.cell(context, widget.entry, widget.live, widget.rowNumber)),
              ],
            ),
            if (_expanded) ...[
              const SizedBox(height: 4),
              Padding(
                padding: const EdgeInsets.only(left: 20),
                child: _RoundBreakdownStrip(entry: widget.entry, live: widget.live),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

/// One round's own real result, whichever source has it -- the live
/// overlay (freshest, only present during an active poll window) or the
/// prediction response's own real, not-status-gated actual.rounds.
({num? scoreToPar, double? totalStrokes})? _actualForRound(int round, FieldParticipantPrediction entry, FieldParticipantLiveResult? live) {
  final liveRound = live?.rounds[round];
  if (liveRound != null) return (scoreToPar: liveRound.scoreToPar, totalStrokes: liveRound.totalStrokes);
  final actualRound = entry.actualRounds[round];
  if (actualRound != null) return (scoreToPar: actualRound.scoreToPar, totalStrokes: actualRound.totalStrokes);
  return null;
}

class _RoundBreakdownStrip extends StatelessWidget {
  const _RoundBreakdownStrip({required this.entry, required this.live});

  final FieldParticipantPrediction entry;
  final FieldParticipantLiveResult? live;

  @override
  Widget build(BuildContext context) {
    // Always all 4 rounds, regardless of how many have real data yet --
    // a stable grid (round 3 is never silently missing just because it
    // hasn't been played) rather than a variable-length list.
    return Wrap(
      spacing: 20,
      runSpacing: 8,
      children: [
        for (var round = 1; round <= 4; round++)
          _RoundCell(round: round, projected: entry.rounds[round]?.value, actual: _actualForRound(round, entry, live), showLabel: true),
      ],
    );
  }
}

class _RoundCell extends StatelessWidget {
  const _RoundCell({required this.round, required this.projected, required this.actual, this.showLabel = false});

  final int round;
  final double? projected; // the round model's own point estimate
  final ({num? scoreToPar, double? totalStrokes})? actual;
  // false for the compact top-level 'THIS RD' column (the round number is
  // already implied by the column header); true inside the full 1-4
  // breakdown, where each cell needs its own "ROUND N" label.
  final bool showLabel;

  @override
  Widget build(BuildContext context) {
    final actualText = Text(
      actual != null ? _formatToPar(actual!.scoreToPar) : '--',
      style: AppTextStyles.metricValue(color: actual != null ? AppColors.ink : AppColors.inkMute),
      maxLines: 1, softWrap: false, overflow: TextOverflow.ellipsis,
    );
    final projectedText = Text(
      projected != null ? '(proj ${_formatToPar(projected)})' : '(proj --)',
      style: AppTextStyles.microLabel(color: AppColors.cyan),
      maxLines: 1, softWrap: false, overflow: TextOverflow.ellipsis,
    );
    // Inside the full breakdown (showLabel), there's room to keep actual
    // and projected on one line next to the "ROUND N" label above them.
    // In the compact top-level 'THIS RD' column, that column is often
    // too narrow for both side by side -- stack them instead, the same
    // "trade width for height" PLAYER's own name+country cell already
    // uses.
    final value = showLabel
        ? Row(mainAxisSize: MainAxisSize.min, children: [actualText, const SizedBox(width: 6), projectedText])
        : Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.center, children: [actualText, projectedText]);
    if (!showLabel) return value;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        Text('ROUND $round', style: AppTextStyles.microLabel(color: AppColors.inkMute)),
        value,
      ],
    );
  }
}

class _PercentText extends StatelessWidget {
  const _PercentText(this.value);

  final double? value;

  @override
  Widget build(BuildContext context) {
    if (value == null) {
      return Text('--', style: AppTextStyles.metricValue(color: AppColors.inkMute), textAlign: TextAlign.center);
    }
    return Text(
      '${(value! * 100).round()}%',
      style: AppTextStyles.metricValue(color: AppColors.violet),
      textAlign: TextAlign.center,
    );
  }
}

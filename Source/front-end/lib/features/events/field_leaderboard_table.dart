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

// Below this width, a real ~150-golfer field with real names/pills/
// stroke counts genuinely doesn't fit across 7 columns -- everything
// silently ellipsizes into illegibility rather than throwing a layout
// exception, which is exactly why the earlier flat mobile-overflow tests
// (checking only for a crash) didn't catch it. Same breakpoint value as
// game_row.dart's own _stackBreakpoint, for the same "narrower than a
// tablet" cutoff, though this table's own content is denser so it needs
// column REDUCTION, not just stacking. THIS RD/TOP 10%/TOP 5% move into
// the expanded per-row detail below this width -- ROUND 1-4 already
// shows the current round's own proj/actual once expanded, so THIS RD's
// own information isn't lost, just no longer duplicated at the top
// level; TOP 10%/TOP 5% get a new home in _ExpandedProbabilities.
const _compactBreakpoint = 600.0;

List<_LeaderboardColumn> _leaderboardColumns(int? par, {required bool compact}) {
  final core = [
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
      // live.status first (freshest, live-poll window only), else this
      // golfer's own real stored status -- NOT inferred from
      // actualFinishPosition's presence (a real current standing exists
      // well before this golfer's own round is actually finished; that
      // was a real bug -- every golfer with any standing at all showed
      // "Finished" regardless of the tournament actually still being in
      // progress).
      _LeaderboardColumn('STATUS', compact ? 1 : 3, (context, entry, live, rowNumber) {
        final status = live?.status ?? entry.actualStatus;
        return Center(child: FieldStatusPill(status: status, dotOnly: compact));
      }),
      // Actual (live-first, else the real cumulative standing) next to the
      // model's own projected FINAL tournament score-to-par -- same
      // actual/projected pairing THIS RD shows for the current round alone,
      // now shown for the whole tournament (was two separate columns,
      // TO PAR and PROJ, split apart from each other for no reason).
      _LeaderboardColumn('TO PAR', 3, (context, entry, live, rowNumber) {
        final scoreToPar = live?.scoreToPar ?? entry.actualScoreToPar;
        final projected = entry.projectedScoreToPar?.value;
        return Center(child: _StandingCell(actual: scoreToPar, projected: projected));
      }),
  ];
  if (compact) return core;
  return [
    ...core,
    // The current round's own proj/actual, visible without expanding
    // the row -- the full 1-4 breakdown is still only in the expanded
    // panel below (_RoundBreakdownStrip), but the round most relevant
    // right now doesn't require a tap to see. Dropped from the
    // COMPACT (mobile) column set below _compactBreakpoint -- ROUND
    // 1-4 already shows the current round's own proj/actual once
    // expanded, so this would just be the same number twice on a
    // screen with no room to spare.
    _LeaderboardColumn('THIS RD', 2, (context, entry, live, rowNumber) {
      final round = _currentRoundNumber(entry);
      if (round == null) {
        return Text('--', style: AppTextStyles.metricValue(color: AppColors.inkMute), textAlign: TextAlign.center);
      }
      return Center(
        child: _RoundCell(
          round: round, projected: entry.rounds[round]?.value, actual: _actualForRound(round, entry, live), par: par,
          thru: _currentThru(entry, live),
        ),
      );
    }),
    _LeaderboardColumn('TOP 10%', 2, (context, entry, live, rowNumber) => _PercentText(entry.top10Probability?.value)),
    _LeaderboardColumn('TOP 5%', 2, (context, entry, live, rowNumber) => _PercentText(entry.top5Probability?.value)),
  ];
}

String _formatToPar(num? scoreToPar) {
  if (scoreToPar == null) return '--';
  final rounded = scoreToPar.round();
  if (rounded == 0) return 'E';
  return rounded > 0 ? '+$rounded' : '$rounded';
}

/// "67 (-3)" -- the stroke count first, its own to-par in parentheses,
/// same framing every real golf leaderboard uses. Falls back to the bare
/// to-par number alone when no stroke count is available at all (no par
/// known for the course, or -- pre-tournament -- nothing to project a
/// stroke count from yet).
String _formatStrokesAndToPar(double? strokes, num? scoreToPar) {
  final toPar = _formatToPar(scoreToPar);
  if (strokes == null) return toPar;
  return '${strokes.round()} ($toPar)';
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
  const FieldLeaderboardTable({super.key, required this.field, this.liveResults = const {}, this.par});

  final List<FieldParticipantPrediction> field;
  // Optional overlay from fieldLiveScoresProvider -- the table itself
  // doesn't own polling, the detail page feeds fresher data in as it
  // arrives (same split GameRow uses for its own isLive/liveState params).
  final Map<String, FieldParticipantLiveResult> liveResults;
  // This tournament's own course par (FieldEventPrediction.par) -- lets
  // every actual/projected cell show "N strokes (to par)" instead of the
  // bare to-par number. Null for an older cached response predating this
  // field; every cell falls back to the bare to-par number in that case.
  final int? par;

  @override
  Widget build(BuildContext context) {
    if (field.isEmpty) {
      return Text('No field available yet.', style: AppTextStyles.body(color: AppColors.inkSub));
    }
    final sorted = _sortedByStanding(field, liveResults);
    return LayoutBuilder(
      builder: (context, constraints) {
        final compact = constraints.maxWidth < _compactBreakpoint;
        final columns = _leaderboardColumns(par, compact: compact);
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
                _LeaderboardRow(
                  entry: sorted[i], live: liveResults[sorted[i].entityId], columns: columns, rowNumber: i + 1,
                  par: par, compact: compact,
                ),
              ],
            ],
          ),
        );
      },
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
/// (_RoundBreakdownStrip) below the standard columns -- on a wide
/// viewport, the current round's own summary is already visible at the
/// top level ('THIS RD'), so the expanded view is purely the FULL
/// history. Below _compactBreakpoint, THIS RD/TOP 10%/TOP 5% aren't in
/// the top-level columns at all (see _leaderboardColumns), so expanding
/// is the ONLY way to see TOP 10%/TOP 5% on a narrow screen -- see
/// _ExpandedProbabilities, shown only when compact.
class _LeaderboardRow extends StatefulWidget {
  const _LeaderboardRow({
    required this.entry, required this.live, required this.columns, required this.rowNumber,
    required this.par, required this.compact,
  });

  final FieldParticipantPrediction entry;
  final FieldParticipantLiveResult? live;
  final List<_LeaderboardColumn> columns;
  final int rowNumber;
  final int? par;
  final bool compact;

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
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    if (widget.compact) ...[
                      _ExpandedProbabilities(entry: widget.entry),
                      const SizedBox(height: 12),
                    ],
                    _RoundBreakdownStrip(entry: widget.entry, live: widget.live, par: widget.par),
                  ],
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

/// TOP 10%/TOP 5% dropped from the compact (mobile) top-level columns --
/// this is their only remaining home on a narrow screen, shown above the
/// ROUND 1-4 breakdown once a row is expanded.
class _ExpandedProbabilities extends StatelessWidget {
  const _ExpandedProbabilities({required this.entry});

  final FieldParticipantPrediction entry;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Text('TOP 10%', style: AppTextStyles.microLabel(color: AppColors.inkMute)),
        const SizedBox(width: 6),
        _PercentText(entry.top10Probability?.value),
        const SizedBox(width: 20),
        Text('TOP 5%', style: AppTextStyles.microLabel(color: AppColors.inkMute)),
        const SizedBox(width: 6),
        _PercentText(entry.top5Probability?.value),
      ],
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

/// Holes completed in the CURRENT round -- live overlay first (freshest
/// during an active poll window), else the prediction response's own
/// real (not status-gated) actual.thru. Only meaningful while this
/// golfer's own status is 'in_progress' -- null otherwise so a finished/
/// not-yet-started golfer never shows a stale "Thru N" from an earlier
/// round.
int? _currentThru(FieldParticipantPrediction entry, FieldParticipantLiveResult? live) {
  final status = live?.status ?? entry.actualStatus;
  if (status != 'in_progress') return null;
  return live?.thru ?? entry.actualThru;
}

class _RoundBreakdownStrip extends StatelessWidget {
  const _RoundBreakdownStrip({required this.entry, required this.live, required this.par});

  final FieldParticipantPrediction entry;
  final FieldParticipantLiveResult? live;
  final int? par;

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
          _RoundCell(
            round: round, projected: entry.rounds[round]?.value, actual: _actualForRound(round, entry, live),
            par: par, showLabel: true,
            thru: round == _currentRoundNumber(entry) ? _currentThru(entry, live) : null,
          ),
      ],
    );
  }
}

class _RoundCell extends StatelessWidget {
  const _RoundCell({
    required this.round, required this.projected, required this.actual, required this.par,
    this.showLabel = false, this.thru,
  });

  final int round;
  final double? projected; // the round model's own point estimate
  final ({num? scoreToPar, double? totalStrokes})? actual;
  // This course's own single-round par -- projected * strokes is
  // (par + projected), unlike TO PAR's own full-tournament (par * 4 +
  // projected) conversion, since a single round has no cut-line
  // ambiguity to worry about.
  final int? par;
  // false for the compact top-level 'THIS RD' column (the round number is
  // already implied by the column header); true inside the full 1-4
  // breakdown, where each cell needs its own "ROUND N" label.
  final bool showLabel;
  // Holes completed so far in THIS round -- only ever non-null for
  // whichever round is this golfer's own currently in-progress one (see
  // _currentThru), never for an already-finished or not-yet-started one.
  final int? thru;

  @override
  Widget build(BuildContext context) {
    final projectedStrokes = (par != null && projected != null) ? (par! + projected!) : null;
    final actualText = Text(
      actual != null ? _formatStrokesAndToPar(actual!.totalStrokes, actual!.scoreToPar) : '--',
      style: AppTextStyles.metricValue(color: actual != null ? AppColors.ink : AppColors.inkMute),
      maxLines: 1, softWrap: false, overflow: TextOverflow.ellipsis,
    );
    final projectedText = Text(
      _formatStrokesAndToPar(projectedStrokes, projected),
      style: AppTextStyles.microLabel(color: AppColors.cyan),
      maxLines: 1, softWrap: false, overflow: TextOverflow.ellipsis,
    );
    final thruCaption = thru != null
        ? Text('Thru $thru', style: AppTextStyles.microLabel(color: AppColors.inkMute), maxLines: 1, overflow: TextOverflow.ellipsis)
        : null;
    // Inside the full breakdown (showLabel), there's room to keep actual
    // and projected on one line next to the "ROUND N" label above them.
    // In the compact top-level 'THIS RD' column, that column is often
    // too narrow for both side by side -- stack them instead, the same
    // "trade width for height" PLAYER's own name+country cell already
    // uses. thruCaption (when present) gets its own line either way --
    // there's never room to fit "Thru N" next to the score inline.
    final value = showLabel
        ? Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(mainAxisSize: MainAxisSize.min, children: [actualText, const SizedBox(width: 6), projectedText]),
            if (thruCaption != null) thruCaption,
          ])
        : Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.center, children: [
            actualText,
            if (thruCaption != null) thruCaption,
            projectedText,
          ]);
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

/// TO PAR column's cell -- same compact actual-over-projected stack
/// _RoundCell uses for THIS RD (showLabel: false), just tournament-level
/// (cumulative standing vs. projected FINAL score-to-par) instead of one
/// round's own actual vs. that round's own projection. Deliberately
/// stays bare to-par (no "N strokes" prefix) even though a real stroke
/// count is often available here too -- unlike a single round, a
/// tournament total has no unambiguous par baseline to convert against
/// (2-round missed-cut vs. 4-round made-cut), so THIS RD/the round
/// breakdown get the "N strokes (to par)" treatment but this column does
/// not -- explicit user call.
class _StandingCell extends StatelessWidget {
  const _StandingCell({required this.actual, required this.projected});

  final num? actual;
  final double? projected;

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.center,
      children: [
        Text(
          _formatToPar(actual), style: AppTextStyles.metricValue(color: actual != null ? AppColors.ink : AppColors.inkMute),
          maxLines: 1, softWrap: false, overflow: TextOverflow.ellipsis,
        ),
        Text(
          _formatToPar(projected),
          style: AppTextStyles.microLabel(color: AppColors.cyan),
          maxLines: 1, softWrap: false, overflow: TextOverflow.ellipsis,
        ),
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

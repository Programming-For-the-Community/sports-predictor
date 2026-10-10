import 'package:flutter/material.dart';

import '../../core/models/field_live_score.dart';
import '../../core/models/field_prediction.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/pga_participant_status.dart';
import '../../core/widgets/fit_text.dart';
import '../../core/widgets/name_with_tag.dart';
import 'leaderboard_sort.dart';

/// PGA's own leaderboard table -- column-spec pattern, same shape as
/// season_page.dart's own _StandingsTable/_StandingsColumn (chosen over
/// the simpler _LeaderboardCard pattern there: a ~150-golfer field needs
/// 6+ compact columns, which is exactly what _StandingsColumn's
/// List<{label, flex, cell}> shape was built for). Public (not private
/// like _StandingsTable) since it's used from field_event_detail_page.dart,
/// a different file.
// Every column header this table can show -- not shared with any other
// file (f1_leaderboard_table.dart's own column set is a genuinely
// different one), but named here instead of typed inline at each of the
// several sites (the _leaderboardColumns list, _ExpandedProbabilities)
// that use them.
abstract final class _FieldColumnLabels {
  static const position = '#';
  static const player = 'PLAYER';
  static const total = 'TOTAL';
  static const projected = 'PROJ';
  static const thisRound = 'THIS RD';
  static const top10 = 'TOP 10%';
  static const top5 = 'TOP 5%';
}

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
/// round is genuinely still being projected (has a prediction in `rounds`
/// but no matching real result in `actualRounds` yet), or, once every
/// predicted round already has a real actual to match it, the most
/// recently completed real round.
///
/// `rounds` is NOT "0 or 1 entries in practice" the way it reads at a
/// glance -- event_prediction.py's own predict_field_event backfills
/// every PLAYED round's own pre-round forecast into it too ("so the
/// ROUND 1-4 breakdown can show it next to the real actual"), on top of
/// whatever applicable_rounds itself still considers live. For a
/// completed tournament that's every round 1-4 at once. Trusting
/// entry.rounds.keys.first would pick round 1's own long-stale forecast
/// as "This Rd" for a golfer who'd already finished the whole tournament
/// -- checking each candidate round against actualRounds is what
/// actually finds the one round, if any, still genuinely unresolved.
int? _currentRoundNumber(FieldParticipantPrediction entry) {
  final stillProjecting = entry.rounds.keys.where((round) => !entry.actualRounds.containsKey(round));
  if (stillProjecting.isNotEmpty) return stillProjecting.reduce((a, b) => a < b ? a : b);
  if (entry.actualRounds.isNotEmpty) return entry.actualRounds.keys.reduce((a, b) => a > b ? a : b);
  return null;
}

// Below this width the table keeps five columns; THIS RD and TOP 5% move
// into each row's expanded detail.
const _compactBreakpoint = 600.0;

/// Falls back to the row's own displayed position rather than '--' --
/// every row gets a real number, whether or not a real tournament
/// standing exists yet.
Widget _positionColumnCell(BuildContext context, FieldParticipantPrediction entry, FieldParticipantLiveResult? live, int rowNumber) {
  final position = live?.finishPosition ?? entry.actualFinishPosition;
  final isTie = live?.isTie ?? false;
  final label = position != null ? (isTie ? 'T$position' : '$position') : '$rowNumber';
  return FitText(
    label, style: AppTextStyles.metricValue(color: AppColors.inkMute), textAlign: TextAlign.center
  );
}

/// A golfer's round status in a few letters: "THRU 12", "F", "CUT", "MDF",
/// "WD". Null before they tee off. live.status first (freshest), else the
/// stored one.
(String, Color)? _statusWords(FieldParticipantPrediction entry, FieldParticipantLiveResult? live) {
  final thru = _currentThru(entry, live);
  return switch (live?.status ?? entry.actualStatus) {
    PgaParticipantStatus.inProgress => (thru == null ? 'IN PROGRESS' : 'THRU $thru', AppColors.live),
    PgaParticipantStatus.finished => ('F', AppColors.inkSub),
    PgaParticipantStatus.cut => ('CUT', AppColors.neg),
    PgaParticipantStatus.madeCutDidNotFinish => ('MDF', AppColors.warn),
    PgaParticipantStatus.withdrawn => ('WD', AppColors.neg),
    _ => null,
  };
}

/// The golfer's name over their country and round status.
Widget _playerColumnCell(BuildContext context, FieldParticipantPrediction entry, FieldParticipantLiveResult? live, int rowNumber) {
  final status = _statusWords(entry, live);
  return NameWithTag(
    name: entry.name ?? entry.entityId, detail: entry.country, tag: status?.$1, tagColor: status?.$2 ?? AppColors.inkSub,
  );
}

/// The real cumulative standing, live-first. TOTAL, not TO PAR -- the term
/// real leaderboards use, and distinct from THIS RD's to-par for the round.
Widget _totalColumnCell(BuildContext context, FieldParticipantPrediction entry, FieldParticipantLiveResult? live, int rowNumber) {
  final scoreToPar = live?.scoreToPar ?? entry.actualScoreToPar;
  return FitText(
    _formatToPar(scoreToPar), style: AppTextStyles.metricValue(color: scoreToPar != null ? AppColors.ink : AppColors.inkMute),
    textAlign: TextAlign.center
  );
}

/// The model's projected final score-to-par, in cyan.
Widget _projectedColumnCell(BuildContext context, FieldParticipantPrediction entry, FieldParticipantLiveResult? live, int rowNumber) {
  return FitText(
    _formatToPar(entry.projectedScoreToPar?.value), style: AppTextStyles.metricValue(color: AppColors.cyan),
    textAlign: TextAlign.center
  );
}

/// The current round's proj/actual; the full 1-4 breakdown is in the
/// expanded panel (_RoundBreakdownStrip).
Widget _thisRoundColumnCell(BuildContext context, FieldParticipantPrediction entry, FieldParticipantLiveResult? live, int rowNumber, int? par) {
  final round = _currentRoundNumber(entry);
  if (round == null) {
    return Text('--', style: AppTextStyles.metricValue(color: AppColors.inkMute), textAlign: TextAlign.center);
  }
  return Center(
    child: _RoundCell(round: round, projected: entry.rounds[round]?.value, actual: _actualForRound(round, entry, live), par: par),
  );
}

List<_LeaderboardColumn> _leaderboardColumns(int? par, {required bool compact}) {
  final top10 = _LeaderboardColumn(
    _FieldColumnLabels.top10, 2, (context, entry, live, rowNumber) => _PercentText(entry.top10Probability?.value),
  );
  const core = [
    _LeaderboardColumn(_FieldColumnLabels.position, 1, _positionColumnCell),
    _LeaderboardColumn(_FieldColumnLabels.player, 4, _playerColumnCell),
    _LeaderboardColumn(_FieldColumnLabels.total, 2, _totalColumnCell),
    _LeaderboardColumn(_FieldColumnLabels.projected, 2, _projectedColumnCell),
  ];
  if (compact) return [...core, top10];
  return [
    ...core,
    _LeaderboardColumn(
      _FieldColumnLabels.thisRound, 3,
      (context, entry, live, rowNumber) => _thisRoundColumnCell(context, entry, live, rowNumber, par),
    ),
    top10,
    _LeaderboardColumn(_FieldColumnLabels.top5, 2, (context, entry, live, rowNumber) => _PercentText(entry.top5Probability?.value)),
  ];
}

String _formatToPar(num? scoreToPar) {
  if (scoreToPar == null) return '--';
  final rounded = scoreToPar.round();
  if (rounded == 0) return 'E';
  return rounded > 0 ? '+$rounded' : '$rounded';
}

/// "67 (-3)" -- the stroke count first, its own to-par in parentheses.
/// Falls back to the bare to-par number when no stroke count is
/// available (no par known for the course, or pre-tournament).
String _formatStrokesAndToPar(double? strokes, num? scoreToPar) {
  final toPar = _formatToPar(scoreToPar);
  if (strokes == null) return toPar;
  return '${strokes.round()} ($toPar)';
}

// live overlay first (freshest during an active poll window), falling
// back to the prediction response's own real (not gated on the whole
// tournament being "completed") current standing -- same precedence the
// TOTAL column already uses.
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
List<FieldParticipantPrediction> _sortedByStanding(List<FieldParticipantPrediction> field, Map<String, FieldParticipantLiveResult> liveResults) =>
    sortedByNullableKey(field, (e) => _standingScoreToPar(e, liveResults[e.entityId]));

/// Where the projected cut falls in `sorted`: the index of the first golfer
/// whose real standing is over it. Null without a projection, once the real
/// cut has been made, or when nobody is on one side of it yet.
int? _projectedCutIndex(
  List<FieldParticipantPrediction> sorted, Map<String, FieldParticipantLiveResult> liveResults, double? projectedCut,
) {
  if (projectedCut == null) return null;
  const cutStatuses = {PgaParticipantStatus.cut, PgaParticipantStatus.madeCutDidNotFinish};
  if (sorted.any((e) => cutStatuses.contains(liveResults[e.entityId]?.status ?? e.actualStatus))) return null;
  final index = sorted.indexWhere((e) {
    final score = _standingScoreToPar(e, liveResults[e.entityId]);
    return score != null && score > projectedCut;
  });
  return index <= 0 ? null : index;
}

class FieldLeaderboardTable extends StatefulWidget {
  const FieldLeaderboardTable({super.key, required this.field, this.liveResults = const {}, this.par, this.projectedCut});

  static const searchHint = 'Find a golfer';

  final List<FieldParticipantPrediction> field;
  // From fieldLiveScoresProvider; the detail page owns the polling.
  final Map<String, FieldParticipantLiveResult> liveResults;
  // This course's par (FieldEventPrediction.par), so a round shows "N
  // strokes (to par)". Null for an older cached response.
  final int? par;
  // The model's projected cut score-to-par, drawn as a line in the table
  // until the real cut is made.
  final double? projectedCut;

  @override
  State<FieldLeaderboardTable> createState() => _FieldLeaderboardTableState();
}

class _FieldLeaderboardTableState extends State<FieldLeaderboardTable> {
  String _query = '';

  Widget _search() => TextField(
        onChanged: (value) => setState(() => _query = value.trim().toLowerCase()),
        style: AppTextStyles.body(color: AppColors.ink),
        decoration: InputDecoration(
          hintText: FieldLeaderboardTable.searchHint,
          hintStyle: AppTextStyles.body(color: AppColors.inkMute),
          prefixIcon: const Icon(Icons.search, size: 18, color: AppColors.inkMute),
          isDense: true,
          contentPadding: const EdgeInsets.symmetric(vertical: 10),
          enabledBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(999), borderSide: const BorderSide(color: AppColors.border),
          ),
          focusedBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(999), borderSide: const BorderSide(color: AppColors.violet),
          ),
        ),
      );

  Widget _cutLine(double cut) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 6),
        child: Row(
          children: [
            Text('PROJ CUT ${_formatToPar(cut)}', style: AppTextStyles.microLabel(color: AppColors.warn)),
            const SizedBox(width: 10),
            const Expanded(child: Divider(height: 1, color: AppColors.warn)),
          ],
        ),
      );

  @override
  Widget build(BuildContext context) {
    final field = widget.field;
    final liveResults = widget.liveResults;
    if (field.isEmpty) {
      return Text('No field available yet.', style: AppTextStyles.body(color: AppColors.inkSub));
    }
    final sorted = _sortedByStanding(field, liveResults);
    // A search keeps each golfer's place in the full field and hides the cut line.
    final cutIndex = _query.isEmpty ? _projectedCutIndex(sorted, liveResults, widget.projectedCut) : null;
    final shown = [
      for (var i = 0; i < sorted.length; i++)
        if (_query.isEmpty || (sorted[i].name ?? sorted[i].entityId).toLowerCase().contains(_query)) i,
    ];
    return LayoutBuilder(
      builder: (context, constraints) {
        final compact = constraints.maxWidth < _compactBreakpoint;
        final columns = _leaderboardColumns(widget.par, compact: compact);
        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Align(
              alignment: Alignment.centerLeft,
              child: ConstrainedBox(constraints: const BoxConstraints(maxWidth: 320), child: _search()),
            ),
            const SizedBox(height: 12),
            Container(
              padding: EdgeInsets.symmetric(horizontal: compact ? 12 : 20),
              decoration: BoxDecoration(
                borderRadius: BorderRadius.circular(16),
                gradient: const LinearGradient(begin: Alignment.topLeft, end: Alignment.bottomRight, colors: AppColors.surfaceGrad),
                border: Border.all(color: AppColors.borderRaised),
              ),
              child: Column(
                children: [
                  Padding(padding: const EdgeInsets.symmetric(vertical: 10), child: _LeaderboardHeaderRow(columns: columns)),
                  if (shown.isEmpty)
                    Padding(
                      padding: const EdgeInsets.symmetric(vertical: 16),
                      child: Text('No golfer matches.', style: AppTextStyles.body(color: AppColors.inkSub)),
                    ),
                  for (final i in shown) ...[
                    if (i == cutIndex) _cutLine(widget.projectedCut!) else const Divider(height: 1, color: AppColors.border),
                    _LeaderboardRow(
                      // Keyed so an expanded row stays with its golfer as a search narrows the list.
                      key: ValueKey(sorted[i].entityId),
                      entry: sorted[i], live: liveResults[sorted[i].entityId], columns: columns, rowNumber: i + 1,
                      par: widget.par, compact: compact,
                    ),
                  ],
                ],
              ),
            ),
          ],
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
            child: FitText(
              columns[i].label,
              style: AppTextStyles.microLabel(),
              textAlign: columns[i].label == _FieldColumnLabels.player ? TextAlign.start : TextAlign.center
            ),
          ),
        ],
      ],
    );
  }
}

/// Tap a row to reveal its full ROUND 1-4 proj-vs-actual strip
/// (_RoundBreakdownStrip) below the standard columns. Below
/// _compactBreakpoint, THIS RD/TOP 10%/TOP 5% aren't in the top-level
/// columns at all (see _leaderboardColumns), so expanding is the only
/// way to see them -- see _ExpandedProbabilities, shown only when compact.
class _LeaderboardRow extends StatefulWidget {
  const _LeaderboardRow({
    super.key, required this.entry, required this.live, required this.columns, required this.rowNumber,
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

/// TOP 5% isn't in the compact columns; it leads a phone row's expanded
/// detail, above the round breakdown.
class _ExpandedProbabilities extends StatelessWidget {
  const _ExpandedProbabilities({required this.entry});

  final FieldParticipantPrediction entry;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Text(_FieldColumnLabels.top5, style: AppTextStyles.microLabel(color: AppColors.inkMute)),
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

/// Holes completed in the current round -- live overlay first, else
/// actual.thru. Only meaningful while this golfer's status is
/// 'in_progress' -- null otherwise.
int? _currentThru(FieldParticipantPrediction entry, FieldParticipantLiveResult? live) {
  final status = live?.status ?? entry.actualStatus;
  if (status != PgaParticipantStatus.inProgress) return null;
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
  // This course's own single-round par -- projected strokes is
  // (par + projected).
  final int? par;
  // false for the compact top-level 'THIS RD' column (the round number is
  // already implied by the column header); true inside the full 1-4
  // breakdown, where each cell needs its own "ROUND N" label.
  final bool showLabel;
  // Holes completed so far in this round -- non-null only for whichever
  // round is this golfer's currently in-progress one.
  final int? thru;

  @override
  Widget build(BuildContext context) {
    final projectedStrokes = (par != null && projected != null) ? (par! + projected!) : null;
    final actualText = FitText(
      actual != null ? _formatStrokesAndToPar(actual!.totalStrokes, actual!.scoreToPar) : '--',
      style: AppTextStyles.metricValue(color: actual != null ? AppColors.ink : AppColors.inkMute)
    );
    final projectedText = FitText(
      _formatStrokesAndToPar(projectedStrokes, projected),
      style: AppTextStyles.microLabel(color: AppColors.cyan)
    );
    final thruCaption = thru != null
        ? Text('Thru $thru', style: AppTextStyles.microLabel(color: AppColors.inkMute))
        : null;
    // Inside the full breakdown (showLabel), actual and projected sit on
    // one line next to the "ROUND N" label above them. In the compact
    // top-level 'THIS RD' column, stack them instead. thruCaption gets
    // its own line either way.
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

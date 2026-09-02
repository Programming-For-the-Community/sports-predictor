import 'package:flutter/material.dart';

import '../../core/models/f1_live_score.dart';
import '../../core/models/f1_prediction.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/f1_status_pill.dart';

/// F1's own driver leaderboard table -- column-spec pattern, same shape
/// as field_leaderboard_table.dart's own _LeaderboardColumn (PGA's). A
/// separate file, not a generalization of that one: F1's own prediction
/// keys (win/podium/finish-or-grid/dnf/qualifying) share no column names
/// with PGA's (top10/top5/score-to-par/rounds), and F1 additionally
/// varies its own column set by event_type (field vs sprint) -- see
/// isSprint below. Live overlay (F1LiveEventState.participants, from
/// f1LiveScoresProvider) is optional and ESPN-sourced -- see
/// f1_live_score.dart's own docstring for why it's a genuinely different
/// shape from PGA's own live overlay, just per-driver running order/
/// winner, no status vocabulary.
// Stable identity for each column, distinct from its own display label --
// _columns' compact-column filter below matches on this, not on label
// text, so renaming a header can never silently break which columns
// survive into the compact layout (real complaint 2026-08-31, same root
// cause as the projected-finish-order ties fix above: a display value
// standing in for an identity).
enum _F1ColumnKey { position, driver, status, finishOrGrid, qualifying, win, podium, dnf }

// Every column header this table can show -- not shared with any other
// file (field_leaderboard_table.dart's own PLAYER/TOTAL/etc. are a
// genuinely different set), but named here instead of typed inline in
// _fullColumns below.
abstract final class _F1ColumnLabels {
  static const position = '#';
  static const driver = 'DRIVER';
  static const status = 'STATUS';
  static const finish = 'FINISH';
  static const grid = 'GRID';
  static const qualifying = 'QUALIFYING';
  static const win = 'WIN%';
  static const podium = 'PODIUM%';
  static const dnf = 'DNF%';
}

class _LeaderboardColumn {
  const _LeaderboardColumn(this.key, this.label, this.flex, this.cell);
  final _F1ColumnKey key;
  final String label;
  final int flex;
  final Widget Function(BuildContext context, F1DriverPrediction entry, F1DriverLiveResult? live, int rowNumber) cell;
}

String _formatPercent(double? value) => value == null ? '--' : '${(value * 100).round()}%';

class _PercentText extends StatelessWidget {
  const _PercentText(this.value);
  final double? value;

  @override
  Widget build(BuildContext context) {
    return Text(
      _formatPercent(value), style: AppTextStyles.metricValue(color: AppColors.violet), textAlign: TextAlign.center,
      maxLines: 1, softWrap: false, overflow: TextOverflow.ellipsis,
    );
  }
}

/// "3" (real, already-happened) in ink, "5" (ESPN's own live running
/// order, mid-session) in the live accent color, or "P5" (still just a
/// model projection) muted -- same "actual, else projected, visually
/// distinguished" idea PGA's own _StandingCell establishes, simplified to
/// a single integer position rather than a to-par score, with the live
/// overlay slotted in between actual and projected (a real, if
/// provisional, running position beats a pre-race model estimate).
///
/// `rank`, when given, is shown instead of rounding `projected` itself --
/// the raw regression value rounded independently per row can collide
/// (two close-but-distinct floats rounding to the same integer), which
/// reads as an impossible shared finishing/grid/qualifying slot (real
/// complaints 2026-08-31 for FINISH/GRID, 2026-09-01 for QUALIFYING).
/// FINISH/GRID get `rank` for free from the field's own row order
/// (event_prediction.py's own _field_sort_key sorts by exactly one of
/// them). QUALIFYING isn't the sort key, so it can't reuse row order --
/// event_prediction.py's own _assign_qualifying_ranks computes an
/// independent rank instead, carried on F1ModelValue.rank.
class _PositionCell extends StatelessWidget {
  const _PositionCell({required this.actual, this.live, required this.projected, this.rank, this.hasResult = false});
  final int? actual;
  final int? live;
  final double? projected;
  final int? rank;
  // True when this driver's own F1ActualResult exists (the race is
  // decided and we have a real recorded outcome for them) even though
  // `actual` itself is null -- a DNF/unclassified driver has no numeric
  // finishPosition/gridPosition, but the race is still over. Falling
  // through to the projected/"P{rank}" branch below in that case shows a
  // stale pre-race prediction as if it still meant something (real
  // complaint 2026-09-02: a DNF driver displayed "P6", implying an
  // ongoing projection, for a race that already ended hours earlier).
  // STATUS's own pill (F1StatusPill) already carries the real DNF/
  // classified signal -- this cell just needs to stay out of its way.
  final bool hasResult;

  @override
  Widget build(BuildContext context) {
    if (actual != null) {
      return Text('$actual', style: AppTextStyles.metricValue(color: AppColors.ink), textAlign: TextAlign.center, maxLines: 1);
    }
    if (live != null) {
      return Text('$live', style: AppTextStyles.metricValue(color: AppColors.live), textAlign: TextAlign.center, maxLines: 1);
    }
    if (hasResult) {
      return Text('--', style: AppTextStyles.metricValue(color: AppColors.inkMute), textAlign: TextAlign.center);
    }
    if (projected != null) {
      return Text(
        'P${rank ?? projected!.round()}', style: AppTextStyles.metricValue(color: AppColors.inkMute), textAlign: TextAlign.center, maxLines: 1,
      );
    }
    return Text('--', style: AppTextStyles.metricValue(color: AppColors.inkMute), textAlign: TextAlign.center);
  }
}

// "Red Bull" from "red_bull" -- last-resort fallback for the rare case
// constructor_name itself came back null (entity lookup failed
// server-side); never shows a raw lowercase/underscored id verbatim.
// Public (not file-private) so f1_event_detail_page.dart's own
// _ConstructorsTable can apply the same fallback to a standalone
// constructor row, not just a driver row's constructor sub-label.
String humanizeF1EntityId(String id) => id.split('_').map((w) => w.isEmpty ? w : '${w[0].toUpperCase()}${w.substring(1)}').join(' ');

// Below this width, a ~20-driver field with real names/pills doesn't fit
// across 6+ columns -- same breakpoint every other leaderboard-shaped
// table in this app uses.
const _compactBreakpoint = 600.0;

List<_LeaderboardColumn> _fullColumns({required bool isSprint}) => [
      _LeaderboardColumn(_F1ColumnKey.position, _F1ColumnLabels.position, 1, (context, entry, live, rowNumber) {
        final actual = isSprint ? entry.actual?.gridPosition : entry.actual?.finishPosition;
        final position = actual ?? live?.order;
        final color = position != null ? (actual != null ? AppColors.inkMute : AppColors.live) : AppColors.inkMute;
        return Text(
          '${position ?? rowNumber}', style: AppTextStyles.metricValue(color: color), textAlign: TextAlign.center,
          maxLines: 1, softWrap: false, overflow: TextOverflow.ellipsis,
        );
      }),
      _LeaderboardColumn(_F1ColumnKey.driver, _F1ColumnLabels.driver, 4, (context, entry, live, rowNumber) {
        final name = entry.name ?? entry.entityId;
        final constructorLabel = entry.constructorName ?? (entry.constructorEntityId != null ? humanizeF1EntityId(entry.constructorEntityId!) : null);
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(name, style: AppTextStyles.body(color: AppColors.ink), maxLines: 1, overflow: TextOverflow.ellipsis),
            if (constructorLabel != null)
              Text(constructorLabel, style: AppTextStyles.microLabel(color: AppColors.inkSub), maxLines: 1, overflow: TextOverflow.ellipsis),
          ],
        );
      }),
      _LeaderboardColumn(_F1ColumnKey.status, _F1ColumnLabels.status, 2,
          (context, entry, live, rowNumber) => Center(child: F1StatusPill(status: entry.actual?.status))),
      isSprint
          ? _LeaderboardColumn(_F1ColumnKey.finishOrGrid, _F1ColumnLabels.grid, 2, (context, entry, live, rowNumber) =>
              _PositionCell(
                actual: entry.actual?.gridPosition, live: live?.order, projected: entry.projectedGridPosition?.value,
                rank: rowNumber, hasResult: entry.actual != null,
              ))
          : _LeaderboardColumn(_F1ColumnKey.finishOrGrid, _F1ColumnLabels.finish, 2, (context, entry, live, rowNumber) =>
              _PositionCell(
                actual: entry.actual?.finishPosition, live: live?.order, projected: entry.projectedFinishPosition?.value,
                rank: rowNumber, hasResult: entry.actual != null,
              )),
      if (!isSprint)
        _LeaderboardColumn(_F1ColumnKey.qualifying, _F1ColumnLabels.qualifying, 2, (context, entry, live, rowNumber) =>
            _PositionCell(
              actual: entry.actual?.qualifyingPosition, projected: entry.projectedQualifyingPosition?.value,
              rank: entry.projectedQualifyingPosition?.rank,
            )),
      _LeaderboardColumn(_F1ColumnKey.win, _F1ColumnLabels.win, 2, (context, entry, live, rowNumber) => _PercentText(entry.winProbability?.value)),
      _LeaderboardColumn(
        _F1ColumnKey.podium, _F1ColumnLabels.podium, 2, (context, entry, live, rowNumber) => _PercentText(entry.podiumProbability?.value),
      ),
      if (!isSprint)
        _LeaderboardColumn(_F1ColumnKey.dnf, _F1ColumnLabels.dnf, 2, (context, entry, live, rowNumber) => _PercentText(entry.dnfProbability?.value)),
    ];

List<_LeaderboardColumn> _columns({required bool isSprint, required bool compact}) {
  final full = _fullColumns(isSprint: isSprint);
  if (!compact) return full;
  // #, DRIVER, WIN% at the top level; STATUS/FINISH-or-GRID/QUALIFYING/
  // PODIUM%/DNF% move into the expanded per-row detail below
  // _compactBreakpoint. Found by key, not label text or a fixed index --
  // QUALIFYING only exists for a field event, so WIN%'s own position in
  // `full` shifts depending on isSprint.
  final winPercent = full.firstWhere((c) => c.key == _F1ColumnKey.win);
  return [full[0], full[1], winPercent];
}

/// Live order first (freshest, only present during/shortly after an
/// active ESPN session -- see f1_live_score.dart's own docstring), falling
/// back to the server's own projected order for a driver not in the live
/// overlay at all -- same "real signal first, else the model's own order"
/// precedent field_leaderboard_table.dart's own _sortedByStanding
/// establishes. A stable sort: ties within either group preserve the
/// original (server) order rather than reshuffling arbitrarily.
List<F1DriverPrediction> _sortedByLiveOrder(List<F1DriverPrediction> field, Map<String, F1DriverLiveResult> liveResults) {
  final indexed = [for (var i = 0; i < field.length; i++) (index: i, entry: field[i])];
  indexed.sort((a, b) {
    final aOrder = liveResults[a.entry.entityId]?.order;
    final bOrder = liveResults[b.entry.entityId]?.order;
    if (aOrder != null && bOrder != null) {
      final cmp = aOrder.compareTo(bOrder);
      return cmp != 0 ? cmp : a.index.compareTo(b.index);
    }
    if (aOrder != null) return -1;
    if (bOrder != null) return 1;
    return a.index.compareTo(b.index);
  });
  return [for (final e in indexed) e.entry];
}

/// Once a race is actually over, row order should reflect the real
/// result, not stay frozen at whatever order the pre-race prediction
/// happened to list drivers in (field's own baseline order --
/// event_prediction.py's own _field_sort_key, computed before the race).
/// _sortedByLiveOrder above only ever fires while liveResults is
/// populated (mid-race); once live polling stops and the checkered flag
/// has fallen, this is what should take over instead of silently
/// reverting to the stale pre-race order (real complaint 2026-09-02: a
/// completed Grand Prix's own row order didn't match the actual
/// finishing order at all). isSprint picks grid vs. finish the same way
/// every other actual-vs-projected cell in this file already does.
/// A driver with no actual position (DNF/unclassified -- entry.actual
/// exists but its own finishPosition/gridPosition is null) sorts after
/// every classified driver, stable amongst themselves in field's own
/// original order -- same "ties preserve original order" rule
/// _sortedByLiveOrder already follows.
List<F1DriverPrediction> _sortedByActualResult(List<F1DriverPrediction> field, bool isSprint) {
  final indexed = [for (var i = 0; i < field.length; i++) (index: i, entry: field[i])];
  int? position(F1DriverPrediction e) => isSprint ? e.actual?.gridPosition : e.actual?.finishPosition;
  indexed.sort((a, b) {
    final aPos = position(a.entry);
    final bPos = position(b.entry);
    if (aPos != null && bPos != null) {
      final cmp = aPos.compareTo(bPos);
      return cmp != 0 ? cmp : a.index.compareTo(b.index);
    }
    if (aPos != null) return -1;
    if (bPos != null) return 1;
    return a.index.compareTo(b.index);
  });
  return [for (final e in indexed) e.entry];
}

class F1LeaderboardTable extends StatelessWidget {
  const F1LeaderboardTable({super.key, required this.field, required this.isSprint, this.liveResults = const {}});

  final List<F1DriverPrediction> field;
  final bool isSprint;
  // Optional overlay from f1LiveScoresProvider -- the table itself doesn't
  // own polling, the detail page feeds fresher data in as it arrives (same
  // split field_leaderboard_table.dart's own liveResults param uses).
  final Map<String, F1DriverLiveResult> liveResults;

  @override
  Widget build(BuildContext context) {
    if (field.isEmpty) {
      return Text('No field available yet.', style: AppTextStyles.body(color: AppColors.inkSub));
    }
    final hasActualResults = field.any((e) => e.actual != null);
    final sorted = liveResults.isNotEmpty
        ? _sortedByLiveOrder(field, liveResults)
        : hasActualResults
            ? _sortedByActualResult(field, isSprint)
            : field;
    return LayoutBuilder(
      builder: (context, constraints) {
        final compact = constraints.maxWidth < _compactBreakpoint;
        final columns = _columns(isSprint: isSprint, compact: compact);
        return Container(
          padding: const EdgeInsets.symmetric(horizontal: 20),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(16),
            gradient: LinearGradient(begin: Alignment.topLeft, end: Alignment.bottomRight, colors: AppColors.surfaceGrad),
            border: Border.all(color: AppColors.borderRaised),
          ),
          child: Column(
            children: [
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 10),
                child: Row(
                  children: [
                    if (compact) const SizedBox(width: 20),
                    for (var i = 0; i < columns.length; i++) ...[
                      if (i > 0) const SizedBox(width: 6),
                      Expanded(
                        flex: columns[i].flex,
                        child: Text(
                          columns[i].label, style: AppTextStyles.microLabel(),
                          textAlign: i == 0 ? TextAlign.start : TextAlign.center,
                          maxLines: 1, softWrap: false, overflow: TextOverflow.ellipsis,
                        ),
                      ),
                    ],
                  ],
                ),
              ),
              for (var i = 0; i < sorted.length; i++) ...[
                const Divider(height: 1, color: AppColors.border),
                _LeaderboardRow(
                  entry: sorted[i], live: liveResults[sorted[i].entityId], columns: columns, rowNumber: i + 1,
                  compact: compact, isSprint: isSprint,
                ),
              ],
            ],
          ),
        );
      },
    );
  }
}

class _LeaderboardRow extends StatefulWidget {
  const _LeaderboardRow({
    required this.entry, required this.live, required this.columns, required this.rowNumber, required this.compact, required this.isSprint,
  });

  final F1DriverPrediction entry;
  final F1DriverLiveResult? live;
  final List<_LeaderboardColumn> columns;
  final int rowNumber;
  final bool compact;
  final bool isSprint;

  @override
  State<_LeaderboardRow> createState() => _LeaderboardRowState();
}

class _LeaderboardRowState extends State<_LeaderboardRow> {
  bool _expanded = false;

  @override
  Widget build(BuildContext context) {
    final row = Row(
      crossAxisAlignment: CrossAxisAlignment.center,
      children: [
        if (widget.compact) ...[
          Icon(_expanded ? Icons.expand_less : Icons.expand_more, size: 18, color: AppColors.inkMute),
          const SizedBox(width: 2),
        ],
        for (var c = 0; c < widget.columns.length; c++) ...[
          if (c > 0) const SizedBox(width: 6),
          Expanded(flex: widget.columns[c].flex, child: widget.columns[c].cell(context, widget.entry, widget.live, widget.rowNumber)),
        ],
      ],
    );
    if (!widget.compact) {
      return Padding(padding: const EdgeInsets.symmetric(vertical: 12), child: row);
    }
    return InkWell(
      onTap: () => setState(() => _expanded = !_expanded),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            row,
            if (_expanded) ...[
              const SizedBox(height: 8),
              Padding(
                padding: const EdgeInsets.only(left: 20),
                child: _ExpandedDetail(entry: widget.entry, live: widget.live, isSprint: widget.isSprint, rowNumber: widget.rowNumber),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _ExpandedDetail extends StatelessWidget {
  const _ExpandedDetail({required this.entry, required this.live, required this.isSprint, required this.rowNumber});
  final F1DriverPrediction entry;
  final F1DriverLiveResult? live;
  final bool isSprint;
  final int rowNumber;

  @override
  Widget build(BuildContext context) {
    final position = isSprint ? entry.actual?.gridPosition : entry.actual?.finishPosition;
    final projected = isSprint ? entry.projectedGridPosition?.value : entry.projectedFinishPosition?.value;
    return Wrap(
      spacing: 20,
      runSpacing: 8,
      children: [
        _Labeled(_F1ColumnLabels.status, F1StatusPill(status: entry.actual?.status)),
        _Labeled(
          isSprint ? _F1ColumnLabels.grid : _F1ColumnLabels.finish,
          _PositionCell(actual: position, live: live?.order, projected: projected, rank: rowNumber),
        ),
        if (!isSprint) _Labeled(_F1ColumnLabels.dnf, _PercentText(entry.dnfProbability?.value)),
        if (!isSprint)
          _Labeled(
            _F1ColumnLabels.qualifying,
            _PositionCell(
              actual: entry.actual?.qualifyingPosition, projected: entry.projectedQualifyingPosition?.value,
              rank: entry.projectedQualifyingPosition?.rank,
            ),
          ),
      ],
    );
  }
}

class _Labeled extends StatelessWidget {
  const _Labeled(this.label, this.child);
  final String label;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Text(label, style: AppTextStyles.microLabel(color: AppColors.inkMute)),
        const SizedBox(width: 6),
        child,
      ],
    );
  }
}

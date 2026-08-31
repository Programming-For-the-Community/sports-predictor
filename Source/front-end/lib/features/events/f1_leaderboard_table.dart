import 'package:flutter/material.dart';

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
/// isSprint below. No live-scores overlay yet -- F1's own live-scores
/// Lambda is still deferred (project-f1-onboarding memory), so this table
/// shows only the cached prediction response's own actual/projected
/// values, not a live-poll overlay the way PGA's own table does.
class _LeaderboardColumn {
  const _LeaderboardColumn(this.label, this.flex, this.cell);
  final String label;
  final int flex;
  final Widget Function(BuildContext context, F1DriverPrediction entry, int rowNumber) cell;
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

/// "3" (real, already-happened) in ink, or "P5" (still just a model
/// projection) muted -- same "actual, else projected, visually
/// distinguished" idea PGA's own _StandingCell establishes, simplified
/// to a single integer position rather than a to-par score.
class _PositionCell extends StatelessWidget {
  const _PositionCell({required this.actual, required this.projected});
  final int? actual;
  final double? projected;

  @override
  Widget build(BuildContext context) {
    if (actual != null) {
      return Text('$actual', style: AppTextStyles.metricValue(color: AppColors.ink), textAlign: TextAlign.center, maxLines: 1);
    }
    if (projected != null) {
      return Text(
        'P${projected!.round()}', style: AppTextStyles.metricValue(color: AppColors.inkMute), textAlign: TextAlign.center, maxLines: 1,
      );
    }
    return Text('--', style: AppTextStyles.metricValue(color: AppColors.inkMute), textAlign: TextAlign.center);
  }
}

// Below this width, a ~20-driver field with real names/pills doesn't fit
// across 6+ columns -- same breakpoint every other leaderboard-shaped
// table in this app uses.
const _compactBreakpoint = 600.0;

List<_LeaderboardColumn> _fullColumns({required bool isSprint}) => [
      _LeaderboardColumn('#', 1, (context, entry, rowNumber) {
        final position = isSprint ? entry.actual?.gridPosition : entry.actual?.finishPosition;
        return Text(
          '${position ?? rowNumber}', style: AppTextStyles.metricValue(color: AppColors.inkMute), textAlign: TextAlign.center,
          maxLines: 1, softWrap: false, overflow: TextOverflow.ellipsis,
        );
      }),
      _LeaderboardColumn('DRIVER', 4, (context, entry, rowNumber) {
        final name = entry.name ?? entry.entityId;
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(name, style: AppTextStyles.body(color: AppColors.ink), maxLines: 1, overflow: TextOverflow.ellipsis),
            if (entry.constructorEntityId != null)
              Text(entry.constructorEntityId!, style: AppTextStyles.microLabel(color: AppColors.inkSub), maxLines: 1, overflow: TextOverflow.ellipsis),
          ],
        );
      }),
      _LeaderboardColumn('STATUS', 2, (context, entry, rowNumber) => Center(child: F1StatusPill(status: entry.actual?.status))),
      isSprint
          ? _LeaderboardColumn('GRID', 2, (context, entry, rowNumber) =>
              _PositionCell(actual: entry.actual?.gridPosition, projected: entry.projectedGridPosition?.value))
          : _LeaderboardColumn('FINISH', 2, (context, entry, rowNumber) =>
              _PositionCell(actual: entry.actual?.finishPosition, projected: entry.projectedFinishPosition?.value)),
      _LeaderboardColumn('WIN%', 2, (context, entry, rowNumber) => _PercentText(entry.winProbability?.value)),
      _LeaderboardColumn('PODIUM%', 2, (context, entry, rowNumber) => _PercentText(entry.podiumProbability?.value)),
      if (!isSprint) _LeaderboardColumn('DNF%', 2, (context, entry, rowNumber) => _PercentText(entry.dnfProbability?.value)),
    ];

List<_LeaderboardColumn> _columns({required bool isSprint, required bool compact}) {
  final full = _fullColumns(isSprint: isSprint);
  if (!compact) return full;
  // #, DRIVER, WIN% at the top level; STATUS/FINISH-or-GRID/PODIUM%/DNF%
  // move into the expanded per-row detail below _compactBreakpoint.
  return [full[0], full[1], full[4]];
}

class F1LeaderboardTable extends StatelessWidget {
  const F1LeaderboardTable({super.key, required this.field, required this.isSprint});

  final List<F1DriverPrediction> field;
  final bool isSprint;

  @override
  Widget build(BuildContext context) {
    if (field.isEmpty) {
      return Text('No field available yet.', style: AppTextStyles.body(color: AppColors.inkSub));
    }
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
              for (var i = 0; i < field.length; i++) ...[
                const Divider(height: 1, color: AppColors.border),
                _LeaderboardRow(entry: field[i], columns: columns, rowNumber: i + 1, compact: compact, isSprint: isSprint),
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
    required this.entry, required this.columns, required this.rowNumber, required this.compact, required this.isSprint,
  });

  final F1DriverPrediction entry;
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
          Expanded(flex: widget.columns[c].flex, child: widget.columns[c].cell(context, widget.entry, widget.rowNumber)),
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
              Padding(padding: const EdgeInsets.only(left: 20), child: _ExpandedDetail(entry: widget.entry, isSprint: widget.isSprint)),
            ],
          ],
        ),
      ),
    );
  }
}

class _ExpandedDetail extends StatelessWidget {
  const _ExpandedDetail({required this.entry, required this.isSprint});
  final F1DriverPrediction entry;
  final bool isSprint;

  @override
  Widget build(BuildContext context) {
    final position = isSprint ? entry.actual?.gridPosition : entry.actual?.finishPosition;
    final projected = isSprint ? entry.projectedGridPosition?.value : entry.projectedFinishPosition?.value;
    return Wrap(
      spacing: 20,
      runSpacing: 8,
      children: [
        _Labeled('STATUS', F1StatusPill(status: entry.actual?.status)),
        _Labeled(isSprint ? 'GRID' : 'FINISH', _PositionCell(actual: position, projected: projected)),
        if (!isSprint) _Labeled('DNF%', _PercentText(entry.dnfProbability?.value)),
        if (!isSprint && (entry.actual?.qualifyingPosition != null || entry.projectedQualifyingPosition != null))
          _Labeled(
            'QUALIFYING',
            _PositionCell(actual: entry.actual?.qualifyingPosition, projected: entry.projectedQualifyingPosition?.value),
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

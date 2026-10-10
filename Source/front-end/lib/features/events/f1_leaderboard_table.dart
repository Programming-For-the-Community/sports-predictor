import 'package:flutter/material.dart';

import '../../core/models/f1_live_score.dart';
import '../../core/models/f1_prediction.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/f1_status_pill.dart';
import '../../core/widgets/fit_text.dart';
import '../../core/widgets/name_with_tag.dart';
import 'leaderboard_sort.dart';

/// F1's driver leaderboard, column-spec pattern like PGA's
/// field_leaderboard_table.dart. Before a session it leads with the model's
/// predicted order; once there is a live running order or a result it leads
/// with the real position and shows the prediction and the gap beside it.
/// A sprint has no qualifying or DNF columns and ranks by grid position.
enum _F1ColumnKey { position, driver, model, gap, qualifying, win, podium, dnf }

abstract final class _F1ColumnLabels {
  static const position = '#';
  static const driver = 'DRIVER';
  static const status = 'STATUS';
  static const predicted = 'PRED';
  static const grid = 'GRID';
  static const gap = '±';
  static const qualifying = 'QUALI';
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

Widget _dash() => Text('--', style: AppTextStyles.metricValue(color: AppColors.inkMute), textAlign: TextAlign.center);

class _PercentText extends StatelessWidget {
  const _PercentText(this.value);
  final double? value;

  @override
  Widget build(BuildContext context) {
    return FitText(
      _formatPercent(value), style: AppTextStyles.metricValue(color: AppColors.violet), textAlign: TextAlign.center
    );
  }
}

/// The model's predicted position, "P5" in cyan. [rank] is shown instead of
/// rounding [projected]: two close raw values can round to the same slot,
/// which reads as an impossible shared position. Finish and grid take their
/// rank from the field's server order; qualifying carries its own
/// (F1ModelValue.rank).
String? _modelLabel(double? projected, int? rank) => projected == null ? null : 'P${rank ?? projected.round()}';

class _ModelCell extends StatelessWidget {
  const _ModelCell({required this.projected, required this.rank});
  final double? projected;
  final int? rank;

  @override
  Widget build(BuildContext context) {
    final label = _modelLabel(projected, rank);
    if (label == null) return _dash();
    return FitText(label, style: AppTextStyles.metricValue(color: AppColors.cyan), textAlign: TextAlign.center);
  }
}

/// A real position in white with the model's in cyan under it, or the
/// model's alone before there is a real one.
class _ActualOverModelCell extends StatelessWidget {
  const _ActualOverModelCell({required this.actual, required this.projected, this.rank});
  final int? actual;
  final double? projected;
  final int? rank;

  @override
  Widget build(BuildContext context) {
    final label = _modelLabel(projected, rank);
    if (actual == null) return label == null ? _dash() : _ModelCell(projected: projected, rank: rank);
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Text('$actual', style: AppTextStyles.metricValue(color: AppColors.ink), textAlign: TextAlign.center, maxLines: 1),
        if (label != null) FitText(label, style: AppTextStyles.microLabel(color: AppColors.cyan), textAlign: TextAlign.center),
      ],
    );
  }
}

/// Places gained (green, up) or lost (red, down) against the model's
/// predicted position; blank without both.
class _GapCell extends StatelessWidget {
  const _GapCell({required this.position, required this.rank});
  final int? position;
  final int? rank;

  @override
  Widget build(BuildContext context) {
    final position = this.position;
    final rank = this.rank;
    if (position == null || rank == null) return const SizedBox.shrink();
    final gained = rank - position;
    if (gained == 0) return FitText('=', style: AppTextStyles.metricValue(color: AppColors.inkSub), textAlign: TextAlign.center);
    final (text, color) = gained > 0 ? ('▲ $gained', AppColors.live) : ('▼ ${-gained}', AppColors.neg);
    return FitText(text, style: AppTextStyles.metricValue(color: color), textAlign: TextAlign.center);
  }
}

// "Red Bull" from "red_bull", for a constructor whose name came back null.
// Public so f1_event_detail_page.dart's constructors table shares it.
String humanizeF1EntityId(String id) => id.split('_').map((w) => w.isEmpty ? w : '${w[0].toUpperCase()}${w.substring(1)}').join(' ');

// Below this width the table keeps four or five columns and moves the rest
// into each row's expanded detail.
const _compactBreakpoint = 600.0;

/// The driver's real position this session: the grid for a sprint, the
/// finish otherwise.
int? _resultPosition(F1DriverPrediction entry, bool isSprint) =>
    isSprint ? entry.actual?.gridPosition : entry.actual?.finishPosition;

double? _projectedPosition(F1DriverPrediction entry, bool isSprint) =>
    isSprint ? entry.projectedGridPosition?.value : entry.projectedFinishPosition?.value;

/// The real position in muted ink, ESPN's live running order in green, or a
/// dash for a driver with a result but no classified position.
Widget _positionColumnCell(F1DriverPrediction entry, F1DriverLiveResult? live, int rowNumber, bool isSprint) {
  final actual = _resultPosition(entry, isSprint);
  final position = actual ?? live?.order;
  if (position == null && entry.actual != null) {
    return Text('–', style: AppTextStyles.metricValue(color: AppColors.inkMute), textAlign: TextAlign.center);
  }
  final color = actual == null && position != null ? AppColors.live : AppColors.inkMute;
  return FitText('${position ?? rowNumber}', style: AppTextStyles.metricValue(color: color), textAlign: TextAlign.center);
}

const _retiredStatuses = {
  F1DriverStatus.dnf: F1DriverStatus.dnfLabel,
  F1DriverStatus.dsq: F1DriverStatus.dsqLabel,
  F1DriverStatus.dns: F1DriverStatus.dnsLabel,
};

/// Driver name over the constructor, with DNF/DSQ/DNS spelled out beside it.
Widget _driverColumnCell(BuildContext context, F1DriverPrediction entry, F1DriverLiveResult? live, int rowNumber) {
  final constructorId = entry.constructorEntityId;
  return NameWithTag(
    name: entry.name ?? entry.entityId,
    detail: entry.constructorName ?? (constructorId == null ? null : humanizeF1EntityId(constructorId)),
    tag: _retiredStatuses[entry.actual?.status],
    tagColor: AppColors.neg,
  );
}

List<_LeaderboardColumn> _columns({
  required bool isSprint, required bool compact, required bool hasResults, required Map<String, int> projectedRanks,
}) {
  final position = _LeaderboardColumn(
    _F1ColumnKey.position, _F1ColumnLabels.position, 1,
    (context, entry, live, rowNumber) => _positionColumnCell(entry, live, rowNumber, isSprint),
  );
  const driver = _LeaderboardColumn(_F1ColumnKey.driver, _F1ColumnLabels.driver, 4, _driverColumnCell);
  final model = _LeaderboardColumn(
    _F1ColumnKey.model, isSprint ? _F1ColumnLabels.grid : _F1ColumnLabels.predicted, 2,
    (context, entry, live, rowNumber) =>
        _ModelCell(projected: _projectedPosition(entry, isSprint), rank: projectedRanks[entry.entityId]),
  );
  final gap = _LeaderboardColumn(
    _F1ColumnKey.gap, _F1ColumnLabels.gap, 2,
    (context, entry, live, rowNumber) => _GapCell(
      position: _resultPosition(entry, isSprint) ?? live?.order,
      rank: _projectedPosition(entry, isSprint) == null ? null : projectedRanks[entry.entityId],
    ),
  );
  final qualifying = _LeaderboardColumn(
    _F1ColumnKey.qualifying, _F1ColumnLabels.qualifying, 2,
    (context, entry, live, rowNumber) => _ActualOverModelCell(
      actual: entry.actual?.qualifyingPosition, projected: entry.projectedQualifyingPosition?.value,
      rank: entry.projectedQualifyingPosition?.rank,
    ),
  );
  final win = _LeaderboardColumn(
    _F1ColumnKey.win, _F1ColumnLabels.win, 2, (context, entry, live, rowNumber) => _PercentText(entry.winProbability?.value),
  );
  final podium = _LeaderboardColumn(
    _F1ColumnKey.podium, _F1ColumnLabels.podium, 2,
    (context, entry, live, rowNumber) => _PercentText(entry.podiumProbability?.value),
  );
  final dnf = _LeaderboardColumn(
    _F1ColumnKey.dnf, _F1ColumnLabels.dnf, 2, (context, entry, live, rowNumber) => _PercentText(entry.dnfProbability?.value),
  );

  if (compact) return hasResults ? [position, driver, model, gap, win] : [model, driver, win, podium];
  return [
    if (hasResults) position,
    if (!hasResults) model,
    driver,
    if (hasResults) ...[model, gap],
    if (!isSprint) qualifying,
    win,
    podium,
    if (!isSprint) dnf,
  ];
}

/// ESPN's live running order first, then the server's projected order for a
/// driver not in the live overlay. Stable, so ties keep the server order.
List<F1DriverPrediction> _sortedByLiveOrder(List<F1DriverPrediction> field, Map<String, F1DriverLiveResult> liveResults) =>
    sortedByNullableKey(field, (e) => liveResults[e.entityId]?.order);

/// Once a race is over, the real result. A driver with no classified
/// position sorts after every classified one, in the field's own order.
List<F1DriverPrediction> _sortedByActualResult(List<F1DriverPrediction> field, bool isSprint) =>
    sortedByNullableKey(field, (e) => _resultPosition(e, isSprint));

List<F1DriverPrediction> _sortedField(
  List<F1DriverPrediction> field, bool isSprint, Map<String, F1DriverLiveResult> liveResults,
) {
  if (liveResults.isNotEmpty) return _sortedByLiveOrder(field, liveResults);
  if (field.any((e) => e.actual != null)) return _sortedByActualResult(field, isSprint);
  return field;
}

Widget _headerRow(List<_LeaderboardColumn> columns, bool compact) {
  return Padding(
    padding: const EdgeInsets.symmetric(vertical: 10),
    child: Row(
      children: [
        if (compact) const SizedBox(width: 20),
        for (var i = 0; i < columns.length; i++) ...[
          if (i > 0) const SizedBox(width: 6),
          Expanded(
            flex: columns[i].flex,
            child: FitText(
              columns[i].label, style: AppTextStyles.microLabel(),
              textAlign: columns[i].key == _F1ColumnKey.driver ? TextAlign.start : TextAlign.center
            ),
          ),
        ],
      ],
    ),
  );
}

class F1LeaderboardTable extends StatelessWidget {
  const F1LeaderboardTable({super.key, required this.field, required this.isSprint, this.liveResults = const {}});

  final List<F1DriverPrediction> field;
  final bool isSprint;
  // From f1LiveScoresProvider; the detail page owns the polling.
  final Map<String, F1DriverLiveResult> liveResults;

  @override
  Widget build(BuildContext context) {
    if (field.isEmpty) {
      return Text('No field available yet.', style: AppTextStyles.body(color: AppColors.inkSub));
    }
    final sorted = _sortedField(field, isSprint, liveResults);
    // `field` arrives in the server's pre-race predicted order, captured
    // before `sorted` reorders it.
    final projectedRanks = {for (var i = 0; i < field.length; i++) field[i].entityId: i + 1};
    final hasResults = liveResults.isNotEmpty || field.any((e) => e.actual != null);
    return LayoutBuilder(
      builder: (context, constraints) {
        final compact = constraints.maxWidth < _compactBreakpoint;
        final columns = _columns(isSprint: isSprint, compact: compact, hasResults: hasResults, projectedRanks: projectedRanks);
        return Container(
          padding: EdgeInsets.symmetric(horizontal: compact ? 12 : 20),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(16),
            gradient: const LinearGradient(begin: Alignment.topLeft, end: Alignment.bottomRight, colors: AppColors.surfaceGrad),
            border: Border.all(color: AppColors.borderRaised),
          ),
          child: Column(
            children: [
              _headerRow(columns, compact),
              for (var i = 0; i < sorted.length; i++) ...[
                const Divider(height: 1, color: AppColors.border),
                _LeaderboardRow(
                  entry: sorted[i], live: liveResults[sorted[i].entityId], columns: columns, rowNumber: i + 1,
                  compact: compact, isSprint: isSprint, hasResults: hasResults,
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
    required this.entry, required this.live, required this.columns, required this.rowNumber, required this.compact,
    required this.isSprint, required this.hasResults,
  });

  final F1DriverPrediction entry;
  final F1DriverLiveResult? live;
  final List<_LeaderboardColumn> columns;
  final int rowNumber;
  final bool compact;
  final bool isSprint;
  final bool hasResults;

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
                child: _ExpandedDetail(entry: widget.entry, isSprint: widget.isSprint, hasResults: widget.hasResults),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

/// What a phone row leaves out: status, qualifying, and the chances not in
/// its columns.
class _ExpandedDetail extends StatelessWidget {
  const _ExpandedDetail({required this.entry, required this.isSprint, required this.hasResults});
  final F1DriverPrediction entry;
  final bool isSprint;
  final bool hasResults;

  @override
  Widget build(BuildContext context) {
    return Wrap(
      spacing: 20,
      runSpacing: 8,
      crossAxisAlignment: WrapCrossAlignment.center,
      children: [
        _Labeled(_F1ColumnLabels.status, F1StatusPill(status: entry.actual?.status)),
        if (!isSprint)
          _Labeled(
            _F1ColumnLabels.qualifying,
            _ActualOverModelCell(
              actual: entry.actual?.qualifyingPosition, projected: entry.projectedQualifyingPosition?.value,
              rank: entry.projectedQualifyingPosition?.rank,
            ),
          ),
        if (hasResults) _Labeled(_F1ColumnLabels.podium, _PercentText(entry.podiumProbability?.value)),
        if (!isSprint) _Labeled(_F1ColumnLabels.dnf, _PercentText(entry.dnfProbability?.value)),
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

/// The session's first three: the model's picks before it, the real podium
/// after it with where the model had each driver.
class F1PodiumStrip extends StatelessWidget {
  const F1PodiumStrip({super.key, required this.field, required this.isSprint});

  static const modelLabel = "MODEL'S TOP THREE";
  static const resultLabel = 'TOP THREE';

  final List<F1DriverPrediction> field;
  final bool isSprint;

  @override
  Widget build(BuildContext context) {
    final projectedRanks = {for (var i = 0; i < field.length; i++) field[i].entityId: i + 1};
    final classified = [for (final entry in field) if (_resultPosition(entry, isSprint) != null) entry]
      ..sort((a, b) => _resultPosition(a, isSprint)!.compareTo(_resultPosition(b, isSprint)!));
    final decided = classified.isNotEmpty;
    final top = (decided ? classified : field).take(3).toList();
    if (top.isEmpty) return const SizedBox.shrink();

    Widget place(int index) {
      final entry = top[index];
      final win = _formatPercent(entry.winProbability?.value);
      final position = decided ? _resultPosition(entry, isSprint)! : index + 1;
      return _PodiumPlace(
        position: 'P$position',
        name: entry.name ?? entry.entityId,
        // After the session: where the model had this driver, and their
        // chance of winning it.
        model: decided ? 'P${projectedRanks[entry.entityId]} · $win' : win,
        modelColor: decided ? AppColors.cyan : AppColors.violet,
      );
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(decided ? resultLabel : modelLabel, style: AppTextStyles.microLabel()),
        const SizedBox(height: 8),
        LayoutBuilder(
          builder: (context, constraints) {
            if (constraints.maxWidth < _compactBreakpoint) {
              return Column(
                children: [for (var i = 0; i < top.length; i++) Padding(padding: EdgeInsets.only(top: i == 0 ? 0 : 8), child: place(i))],
              );
            }
            return Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                for (var i = 0; i < top.length; i++) ...[if (i > 0) const SizedBox(width: 10), Expanded(child: place(i))],
              ],
            );
          },
        ),
      ],
    );
  }
}

class _PodiumPlace extends StatelessWidget {
  const _PodiumPlace({required this.position, required this.name, required this.model, required this.modelColor});
  final String position;
  final String name;
  final String model;
  final Color modelColor;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: AppColors.border),
      ),
      child: Row(
        children: [
          Text(position, style: AppTextStyles.metricValue(color: AppColors.inkMute)),
          const SizedBox(width: 10),
          Expanded(child: Text(name, style: AppTextStyles.body(color: AppColors.ink))),
          const SizedBox(width: 10),
          Text(model, style: AppTextStyles.metricValue(color: modelColor)),
        ],
      ),
    );
  }
}

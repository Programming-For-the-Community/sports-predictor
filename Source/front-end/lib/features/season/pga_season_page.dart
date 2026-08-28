import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/data/pga_season_repository.dart';
import '../../core/models/pga_season_projection.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';

/// PGA's own /:sport/season page -- a FedEx Cup points-standings table,
/// not a bracket. Different shape from season_page.dart (no division/
/// conference grouping, no playoff tree), so this is a standalone page.
class PgaSeasonPage extends ConsumerWidget {
  const PgaSeasonPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final projection = ref.watch(pgaSeasonProjectionProvider);

    return SingleChildScrollView(
      padding: const EdgeInsets.all(24),
      child: projection.when(
        data: (season) {
          if (season.standings.isEmpty) {
            return Text('No FedEx Cup standings yet.', style: AppTextStyles.body(color: AppColors.inkSub));
          }
          return Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('${season.season} FEDEX CUP', style: AppTextStyles.sectionTitle(color: AppColors.violet)),
              const SizedBox(height: 4),
              Text(
                season.simulations > 0
                    ? 'Projected from ${season.simulations} season simulations'
                    : 'Season complete -- real final standings',
                style: AppTextStyles.microLabel(color: AppColors.inkSub),
              ),
              const SizedBox(height: 16),
              _StandingsTable(standings: season.standings),
            ],
          );
        },
        loading: () => const Center(child: Padding(padding: EdgeInsets.all(40), child: CircularProgressIndicator())),
        error: (error, _) =>
            Text('Couldn\'t load the FedEx Cup standings: $error', style: AppTextStyles.body(color: AppColors.neg)),
      ),
    );
  }
}

class _Column {
  const _Column(this.label, this.flex, this.cell);
  final String label;
  final int flex;
  final Widget Function(BuildContext, PgaFedexStanding, int rank) cell;
}

String _formatPoints(double value) => value >= 1000 ? value.round().toString() : value.toStringAsFixed(1);

String _formatPercent(double value) => '${(value * 100).round()}%';

// Below this width, all 7 columns (#, GOLFER, POINTS, ST. JUDE%, BMW%,
// TOUR CH.%, CHAMP%) don't fit -- same breakpoint as
// field_leaderboard_table.dart's own _compactBreakpoint. The 3 Playoffs-
// field probabilities move into an expanded per-row detail below this
// width; CHAMP% stays at the top level as the headline stat.
const _compactBreakpoint = 600.0;

List<_Column> _fullColumns() => [
      _Column('#', 1, (context, row, rank) => Text(
            '$rank', style: AppTextStyles.metricValue(color: AppColors.inkMute), textAlign: TextAlign.center,
          )),
      _Column('GOLFER', 4, (context, row, rank) => Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(row.name ?? row.entityId, style: AppTextStyles.body(color: AppColors.ink), maxLines: 1, overflow: TextOverflow.ellipsis),
              if (row.country != null) Text(row.country!, style: AppTextStyles.microLabel(color: AppColors.inkSub)),
            ],
          )),
      _Column('POINTS', 2, (context, row, rank) => Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.center,
            children: [
              Text(_formatPoints(row.currentPoints), style: AppTextStyles.metricValue(color: AppColors.ink), maxLines: 1),
              Text(_formatPoints(row.projectedPoints), style: AppTextStyles.microLabel(color: AppColors.cyan), maxLines: 1),
            ],
          )),
      _Column('ST. JUDE%', 2, (context, row, rank) => _PercentText(row.fedexStJudeProbability)),
      _Column('BMW%', 2, (context, row, rank) => _PercentText(row.bmwProbability)),
      _Column('TOUR CH.%', 2, (context, row, rank) => _PercentText(row.tourChampionshipProbability)),
      _Column('CHAMP%', 2, (context, row, rank) => _PercentText(row.championProbability)),
    ];

List<_Column> _columns({required bool compact}) {
  final full = _fullColumns();
  if (!compact) return full;
  // #, GOLFER, POINTS, CHAMP% -- ST. JUDE%/BMW%/TOUR CH.% move into
  // _ExpandedPlayoffOdds, shown only once a row is expanded.
  return [full[0], full[1], full[2], full[6]];
}

class _StandingsTable extends StatelessWidget {
  const _StandingsTable({required this.standings});

  final List<PgaFedexStanding> standings;

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final compact = constraints.maxWidth < _compactBreakpoint;
        final columns = _columns(compact: compact);
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
                    // Leading space matching the expand-chevron column below,
                    // so header labels line up with their own cell.
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
              for (var i = 0; i < standings.length; i++) ...[
                const Divider(height: 1, color: AppColors.border),
                _StandingsRow(standing: standings[i], columns: columns, rank: i + 1, compact: compact),
              ],
            ],
          ),
        );
      },
    );
  }
}

/// A plain, non-expandable row on a wide viewport (every column is
/// already visible). Below _compactBreakpoint, tapping reveals
/// _ExpandedPlayoffOdds -- the only remaining way to see ST. JUDE%/BMW%/
/// TOUR CH.% on a narrow screen.
class _StandingsRow extends StatefulWidget {
  const _StandingsRow({required this.standing, required this.columns, required this.rank, required this.compact});

  final PgaFedexStanding standing;
  final List<_Column> columns;
  final int rank;
  final bool compact;

  @override
  State<_StandingsRow> createState() => _StandingsRowState();
}

class _StandingsRowState extends State<_StandingsRow> {
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
          Expanded(flex: widget.columns[c].flex, child: widget.columns[c].cell(context, widget.standing, widget.rank)),
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
              Padding(padding: const EdgeInsets.only(left: 20), child: _ExpandedPlayoffOdds(standing: widget.standing)),
            ],
          ],
        ),
      ),
    );
  }
}

class _ExpandedPlayoffOdds extends StatelessWidget {
  const _ExpandedPlayoffOdds({required this.standing});

  final PgaFedexStanding standing;

  @override
  Widget build(BuildContext context) {
    return Wrap(
      spacing: 20,
      runSpacing: 8,
      children: [
        _LabeledPercent('ST. JUDE%', standing.fedexStJudeProbability),
        _LabeledPercent('BMW%', standing.bmwProbability),
        _LabeledPercent('TOUR CH.%', standing.tourChampionshipProbability),
      ],
    );
  }
}

class _LabeledPercent extends StatelessWidget {
  const _LabeledPercent(this.label, this.value);
  final String label;
  final double value;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Text(label, style: AppTextStyles.microLabel(color: AppColors.inkMute)),
        const SizedBox(width: 6),
        _PercentText(value),
      ],
    );
  }
}

class _PercentText extends StatelessWidget {
  const _PercentText(this.value);
  final double value;

  @override
  Widget build(BuildContext context) {
    return Text(
      _formatPercent(value), style: AppTextStyles.metricValue(color: AppColors.violet), textAlign: TextAlign.center,
      maxLines: 1, softWrap: false, overflow: TextOverflow.ellipsis,
    );
  }
}

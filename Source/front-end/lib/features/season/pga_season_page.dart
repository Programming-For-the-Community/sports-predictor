import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/data/pga_season_repository.dart';
import '../../core/models/pga_season_projection.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';

/// PGA's own /:sport/season page -- a FedEx Cup points-standings table,
/// not a bracket. Genuinely different shape from season_page.dart (no
/// division/conference grouping, no playoff tree -- a golfer has neither;
/// see pga_season_projection.dart's own docstring), so this is a
/// standalone page, not a variant of that ~2000-line file (most of which
/// is bracket-CustomPainter machinery that doesn't apply here at all).
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

List<_Column> _columns() => [
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

class _StandingsTable extends StatelessWidget {
  const _StandingsTable({required this.standings});

  final List<PgaFedexStanding> standings;

  @override
  Widget build(BuildContext context) {
    final columns = _columns();
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
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 12),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.center,
                children: [
                  for (var c = 0; c < columns.length; c++) ...[
                    if (c > 0) const SizedBox(width: 6),
                    Expanded(flex: columns[c].flex, child: columns[c].cell(context, standings[i], i + 1)),
                  ],
                ],
              ),
            ),
          ],
        ],
      ),
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

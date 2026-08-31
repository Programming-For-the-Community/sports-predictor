import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/data/f1_season_repository.dart';
import '../../core/models/f1_season_projection.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';

enum _StandingsTab { drivers, constructors }

/// F1's own /:sport/season page -- TWO points-standings tables (Drivers'
/// and Constructors' Championship) from the same simulated pass, not one
/// -- see f1_season_projection.dart's own docstring for why this can't
/// reuse PgaSeasonPage (one table, no team dimension at all) or the
/// shared bracket-based SeasonPage (division/conference groupings, no
/// analog here).
///
/// Own Drivers/Constructors tab toggle, same pill-toggle shape field_
/// event_list_page.dart's own Upcoming/Completed toggle uses -- both
/// tables stacked on one scroll (the original shape) got hard to scan
/// once a real 22-row drivers' table pushed the constructors' table well
/// below the fold; one table on screen at a time reads far better.
class F1SeasonPage extends ConsumerStatefulWidget {
  const F1SeasonPage({super.key});

  @override
  ConsumerState<F1SeasonPage> createState() => _F1SeasonPageState();
}

class _F1SeasonPageState extends ConsumerState<F1SeasonPage> {
  _StandingsTab _tab = _StandingsTab.drivers;

  void _setTab(_StandingsTab tab) => setState(() => _tab = tab);

  @override
  Widget build(BuildContext context) {
    final projection = ref.watch(f1SeasonProjectionProvider);

    return SingleChildScrollView(
      padding: const EdgeInsets.all(24),
      child: projection.when(
        data: (season) {
          if (season.driverStandings.isEmpty) {
            return Text('No championship standings yet.', style: AppTextStyles.body(color: AppColors.inkSub));
          }
          return Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('${season.season} CHAMPIONSHIP', style: AppTextStyles.sectionTitle(color: AppColors.violet)),
              const SizedBox(height: 4),
              Text(
                season.simulations > 0
                    ? 'Projected from ${season.simulations} season simulations'
                    : 'Season complete -- real final standings',
                style: AppTextStyles.microLabel(color: AppColors.inkSub),
              ),
              const SizedBox(height: 20),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  _StandingsTabToggle(label: 'Drivers\' Championship', selected: _tab == _StandingsTab.drivers, onTap: () => _setTab(_StandingsTab.drivers)),
                  _StandingsTabToggle(
                    label: 'Constructors\' Championship',
                    selected: _tab == _StandingsTab.constructors,
                    onTap: () => _setTab(_StandingsTab.constructors),
                  ),
                ],
              ),
              const SizedBox(height: 12),
              if (_tab == _StandingsTab.drivers)
                _DriverStandingsTable(standings: season.driverStandings)
              else if (season.constructorStandings.isEmpty)
                Text('No constructor standings yet.', style: AppTextStyles.body(color: AppColors.inkSub))
              else
                _ConstructorStandingsTable(standings: season.constructorStandings),
            ],
          );
        },
        loading: () => const Center(child: Padding(padding: EdgeInsets.all(40), child: CircularProgressIndicator())),
        error: (error, _) =>
            Text('Couldn\'t load the championship standings: $error', style: AppTextStyles.body(color: AppColors.neg)),
      ),
    );
  }
}

class _StandingsTabToggle extends StatelessWidget {
  const _StandingsTabToggle({required this.label, required this.selected, required this.onTap});
  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(999),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
        decoration: BoxDecoration(
          color: selected ? AppColors.surface : null,
          border: Border.all(color: selected ? AppColors.violet : AppColors.border),
          borderRadius: BorderRadius.circular(999),
        ),
        child: Text(label, style: AppTextStyles.microLabel(color: selected ? AppColors.violet : AppColors.inkMute)),
      ),
    );
  }
}

String _formatPoints(double value) => value >= 1000 ? value.round().toString() : value.toStringAsFixed(1);

String _formatPercent(double value) => '${(value * 100).round()}%';

class _DriverStandingsTable extends StatelessWidget {
  const _DriverStandingsTable({required this.standings});
  final List<F1DriverStanding> standings;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 20),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(16),
        gradient: LinearGradient(begin: Alignment.topLeft, end: Alignment.bottomRight, colors: AppColors.surfaceGrad),
        border: Border.all(color: AppColors.borderRaised),
      ),
      child: Column(
        children: [
          const Padding(
            padding: EdgeInsets.symmetric(vertical: 10),
            child: _HeaderRow(labels: ['#', 'DRIVER', 'POINTS', 'CHAMP%'], flexes: [1, 4, 2, 2]),
          ),
          for (var i = 0; i < standings.length; i++) ...[
            const Divider(height: 1, color: AppColors.border),
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 12),
              child: Row(
                children: [
                  Expanded(flex: 1, child: Text('${i + 1}', style: AppTextStyles.metricValue(color: AppColors.inkMute), textAlign: TextAlign.center)),
                  const SizedBox(width: 6),
                  Expanded(
                    flex: 4,
                    child: Text(
                      standings[i].name ?? standings[i].entityId, style: AppTextStyles.body(color: AppColors.ink),
                      maxLines: 1, overflow: TextOverflow.ellipsis,
                    ),
                  ),
                  const SizedBox(width: 6),
                  Expanded(
                    flex: 2,
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Text(_formatPoints(standings[i].currentPoints), style: AppTextStyles.metricValue(color: AppColors.ink), maxLines: 1),
                        Text(_formatPoints(standings[i].projectedPoints), style: AppTextStyles.microLabel(color: AppColors.cyan), maxLines: 1),
                      ],
                    ),
                  ),
                  const SizedBox(width: 6),
                  Expanded(
                    flex: 2,
                    child: Text(
                      _formatPercent(standings[i].championProbability), style: AppTextStyles.metricValue(color: AppColors.violet),
                      textAlign: TextAlign.center, maxLines: 1, softWrap: false, overflow: TextOverflow.ellipsis,
                    ),
                  ),
                ],
              ),
            ),
          ],
        ],
      ),
    );
  }
}

class _ConstructorStandingsTable extends StatelessWidget {
  const _ConstructorStandingsTable({required this.standings});
  final List<F1ConstructorStanding> standings;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 20),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(16),
        gradient: LinearGradient(begin: Alignment.topLeft, end: Alignment.bottomRight, colors: AppColors.surfaceGrad),
        border: Border.all(color: AppColors.borderRaised),
      ),
      child: Column(
        children: [
          const Padding(
            padding: EdgeInsets.symmetric(vertical: 10),
            child: _HeaderRow(labels: ['#', 'CONSTRUCTOR', 'POINTS', 'CHAMP%'], flexes: [1, 4, 2, 2]),
          ),
          for (var i = 0; i < standings.length; i++) ...[
            const Divider(height: 1, color: AppColors.border),
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 12),
              child: Row(
                children: [
                  Expanded(flex: 1, child: Text('${i + 1}', style: AppTextStyles.metricValue(color: AppColors.inkMute), textAlign: TextAlign.center)),
                  const SizedBox(width: 6),
                  Expanded(
                    flex: 4,
                    child: Text(
                      standings[i].name ?? standings[i].entityId, style: AppTextStyles.body(color: AppColors.ink),
                      maxLines: 1, overflow: TextOverflow.ellipsis,
                    ),
                  ),
                  const SizedBox(width: 6),
                  Expanded(
                    flex: 2,
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Text(_formatPoints(standings[i].currentPoints), style: AppTextStyles.metricValue(color: AppColors.ink), maxLines: 1),
                        Text(_formatPoints(standings[i].projectedPoints), style: AppTextStyles.microLabel(color: AppColors.cyan), maxLines: 1),
                      ],
                    ),
                  ),
                  const SizedBox(width: 6),
                  Expanded(
                    flex: 2,
                    child: Text(
                      _formatPercent(standings[i].championProbability), style: AppTextStyles.metricValue(color: AppColors.violet),
                      textAlign: TextAlign.center, maxLines: 1, softWrap: false, overflow: TextOverflow.ellipsis,
                    ),
                  ),
                ],
              ),
            ),
          ],
        ],
      ),
    );
  }
}

class _HeaderRow extends StatelessWidget {
  const _HeaderRow({required this.labels, required this.flexes});
  final List<String> labels;
  final List<int> flexes;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        for (var i = 0; i < labels.length; i++) ...[
          if (i > 0) const SizedBox(width: 6),
          Expanded(
            flex: flexes[i],
            child: Text(
              labels[i], style: AppTextStyles.microLabel(),
              textAlign: i == 0 ? TextAlign.center : (i == 1 ? TextAlign.start : TextAlign.center),
              maxLines: 1, softWrap: false, overflow: TextOverflow.ellipsis,
            ),
          ),
        ],
      ],
    );
  }
}

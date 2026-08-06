import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/data/season_repository.dart';
import '../../core/models/season_projection.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../static/nfl_team_colors.dart';

// Source of truth is Terraform/scheduler-nfl-train-player-prop-model.tf's
// nfl_player_prop_stats map -- same duplication handler.py's own
// PLAYER_PROP_STATS accepts, since there's no model registry to read
// display labels from at runtime either.
const _statLabels = {
  'passing_yards': 'Passing Yards',
  'passing_touchdowns': 'Passing TDs',
  'rushing_yards': 'Rushing Yards',
  'rushing_touchdowns': 'Rushing TDs',
  'receiving_yards': 'Receiving Yards',
  'receiving_touchdowns': 'Receiving TDs',
  'defensive_sacks': 'Sacks',
};

// Conventional division reading order -- teams.division (server-side, see
// season_projection.py) only carries the division name itself, not a
// display order, so this is what sorts the section headings below.
const _divisionOrder = [
  'AFC East', 'AFC North', 'AFC South', 'AFC West',
  'NFC East', 'NFC North', 'NFC South', 'NFC West',
];

/// Buckets standings by division, preserving each team's relative order --
/// standings arrives already sorted by projected_wins descending (see
/// SeasonProjection's own doc comment), so each division's own bucket is
/// automatically best-to-worst with no separate sort needed here.
List<MapEntry<String, List<TeamStanding>>> _groupByDivision(List<TeamStanding> standings) {
  final byDivision = <String, List<TeamStanding>>{};
  for (final team in standings) {
    byDivision.putIfAbsent(team.division ?? 'Other', () => []).add(team);
  }
  final divisions = byDivision.keys.toList()
    ..sort((a, b) {
      final ai = _divisionOrder.indexOf(a);
      final bi = _divisionOrder.indexOf(b);
      if (ai == -1 && bi == -1) return a.compareTo(b);
      if (ai == -1) return 1;
      if (bi == -1) return -1;
      return ai.compareTo(bi);
    });
  return [for (final division in divisions) MapEntry(division, byDivision[division]!)];
}

class SeasonPage extends ConsumerStatefulWidget {
  const SeasonPage({super.key, required this.sportId});

  final String sportId;

  @override
  ConsumerState<SeasonPage> createState() => _SeasonPageState();
}

class _SeasonPageState extends ConsumerState<SeasonPage> {
  String _tab = 'standings';

  @override
  Widget build(BuildContext context) {
    final projection = ref.watch(seasonProjectionProvider(widget.sportId));

    return SingleChildScrollView(
      padding: const EdgeInsets.all(24),
      child: projection.when(
        data: (season) => Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(
              season.season != null ? '${season.season} Season' : 'Season',
              style: AppTextStyles.pageH1(),
            ),
            const SizedBox(height: 20),
            // Standings and player props each stand alone (a toggle, not
            // both stacked on one page) -- both are already tall multi-
            // column sections on their own, and stacking them turns this
            // into a very long scroll for no reason once a viewer only
            // wants one or the other.
            Row(
              children: [
                _StatusToggle(
                  label: 'Standings & Playoff Odds',
                  selected: _tab == 'standings',
                  onTap: () => setState(() => _tab = 'standings'),
                ),
                const SizedBox(width: 8),
                _StatusToggle(
                  label: 'Player Prop Leaders',
                  selected: _tab == 'props',
                  onTap: () => setState(() => _tab = 'props'),
                ),
              ],
            ),
            const SizedBox(height: 20),
            if (_tab == 'standings')
              // Fixed-width division cards in a Wrap -- multiple divisions
              // per row on a wide screen, same pattern the leaderboard
              // cards use, instead of one division per full-width row.
              Wrap(
                spacing: 20,
                runSpacing: 20,
                children: [
                  for (final division in _groupByDivision(season.standings))
                    SizedBox(
                      width: 480,
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Padding(
                            padding: const EdgeInsets.only(bottom: 8),
                            child: Text(division.key.toUpperCase(), style: AppTextStyles.microLabel(color: AppColors.cyan)),
                          ),
                          _StandingsTable(standings: division.value),
                        ],
                      ),
                    ),
                ],
              )
            else
              _Leaderboards(leaderboards: season.leaderboards),
          ],
        ),
        loading: () => const Center(child: Padding(padding: EdgeInsets.all(40), child: CircularProgressIndicator())),
        error: (error, _) =>
            Text('Couldn\'t load season projection: $error', style: AppTextStyles.body(color: AppColors.neg)),
      ),
    );
  }
}

// Same toggle-pill shape as events/event_list_page.dart's own
// _StatusToggle -- not shared code between the two pages, but the
// smallest of the two features (StatelessWidget wrapping InkWell) doesn't
// carry its own weight as a cross-page core/widgets export yet.
class _StatusToggle extends StatelessWidget {
  const _StatusToggle({required this.label, required this.selected, required this.onTap});
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
          border: Border.all(color: selected ? AppColors.cyan : AppColors.border),
          borderRadius: BorderRadius.circular(999),
        ),
        child: Text(
          label,
          style: AppTextStyles.microLabel(color: selected ? AppColors.cyan : AppColors.inkMute),
        ),
      ),
    );
  }
}

class _StandingsTable extends StatelessWidget {
  const _StandingsTable({required this.standings});

  final List<TeamStanding> standings;

  @override
  Widget build(BuildContext context) {
    if (standings.isEmpty) {
      return Text('No standings available yet.', style: AppTextStyles.body(color: AppColors.inkSub));
    }
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 20),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(16),
        gradient: LinearGradient(begin: Alignment.topLeft, end: Alignment.bottomRight, colors: AppColors.surfaceGrad),
        border: Border.all(color: AppColors.borderRaised),
      ),
      child: Column(
        children: [
          const Padding(padding: EdgeInsets.symmetric(vertical: 10), child: _StandingsHeaderRow()),
          for (final team in standings) ...[
            const Divider(height: 1, color: AppColors.border),
            Padding(padding: const EdgeInsets.symmetric(vertical: 12), child: _StandingsRow(team: team)),
          ],
        ],
      ),
    );
  }
}

class _StandingsHeaderRow extends StatelessWidget {
  const _StandingsHeaderRow();

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Expanded(flex: 3, child: Text('TEAM', style: AppTextStyles.microLabel())),
        Expanded(flex: 2, child: Text('RECORD', style: AppTextStyles.microLabel(), textAlign: TextAlign.center)),
        Expanded(flex: 2, child: Text('PROJ W', style: AppTextStyles.microLabel(), textAlign: TextAlign.center)),
        Expanded(flex: 2, child: Text('DIV %', style: AppTextStyles.microLabel(), textAlign: TextAlign.center)),
        Expanded(flex: 2, child: Text('PLAYOFF %', style: AppTextStyles.microLabel(), textAlign: TextAlign.center)),
        Expanded(flex: 2, child: Text('SB %', style: AppTextStyles.microLabel(), textAlign: TextAlign.center)),
      ],
    );
  }
}

class _StandingsRow extends StatelessWidget {
  const _StandingsRow({required this.team});

  final TeamStanding team;

  @override
  Widget build(BuildContext context) {
    final info = nflTeam(team.teamId);
    return Row(
      children: [
        Expanded(
          flex: 3,
          child: Row(
            children: [
              Container(width: 8, height: 8, decoration: BoxDecoration(shape: BoxShape.circle, color: info.primary)),
              const SizedBox(width: 10),
              Text(info.abbreviation, style: AppTextStyles.body(color: AppColors.ink)),
            ],
          ),
        ),
        Expanded(
          flex: 2,
          child: Text('${team.wins}-${team.losses}', style: AppTextStyles.metricValue(), textAlign: TextAlign.center),
        ),
        Expanded(
          flex: 2,
          child: Text(
            team.projectedWins.toStringAsFixed(1),
            style: AppTextStyles.metricValue(color: AppColors.cyan),
            textAlign: TextAlign.center,
          ),
        ),
        Expanded(flex: 2, child: _PercentText(team.divisionWinnerProbability)),
        Expanded(flex: 2, child: _PercentText(team.playoffProbability)),
        Expanded(flex: 2, child: _PercentText(team.championshipProbability)),
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
      '${(value * 100).round()}%',
      style: AppTextStyles.metricValue(color: value >= 0.5 ? AppColors.cyan : AppColors.inkSub),
      textAlign: TextAlign.center,
    );
  }
}

class _Leaderboards extends StatelessWidget {
  const _Leaderboards({required this.leaderboards});

  final Map<String, List<LeaderboardEntry>>? leaderboards;

  @override
  Widget build(BuildContext context) {
    final boards = leaderboards;
    if (boards == null) {
      return Text(
        'Leaderboards aren\'t available right now -- check back shortly.',
        style: AppTextStyles.body(color: AppColors.inkSub),
      );
    }
    final cards = [
      for (final entry in _statLabels.entries)
        if (boards[entry.key]?.isNotEmpty ?? false)
          SizedBox(width: 320, child: _LeaderboardCard(label: entry.value, entries: boards[entry.key]!)),
    ];
    if (cards.isEmpty) {
      return Text('No leaderboard data yet.', style: AppTextStyles.body(color: AppColors.inkSub));
    }
    return Wrap(spacing: 20, runSpacing: 20, children: cards);
  }
}

class _LeaderboardCard extends StatelessWidget {
  const _LeaderboardCard({required this.label, required this.entries});

  final String label;
  final List<LeaderboardEntry> entries;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(16),
        gradient: LinearGradient(begin: Alignment.topLeft, end: Alignment.bottomRight, colors: AppColors.surfaceGrad),
        border: Border.all(color: AppColors.borderRaised),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label.toUpperCase(), style: AppTextStyles.microLabel(color: AppColors.cyan)),
          const SizedBox(height: 12),
          for (var i = 0; i < entries.length; i++)
            Padding(
              padding: EdgeInsets.only(top: i == 0 ? 0 : 8),
              child: Row(
                children: [
                  SizedBox(width: 20, child: Text('${i + 1}', style: AppTextStyles.microLabel())),
                  Expanded(
                    child: Text(
                      entries[i].displayName,
                      style: AppTextStyles.body(color: AppColors.ink),
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                  Text(entries[i].projectedTotal.toStringAsFixed(0), style: AppTextStyles.metricValue()),
                ],
              ),
            ),
        ],
      ),
    );
  }
}

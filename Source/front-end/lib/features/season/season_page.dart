import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/data/season_repository.dart';
import '../../core/models/season_projection.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/conference_filter_field.dart';
import '../../core/widgets/responsive.dart';
import '../../core/widgets/team_color_dot.dart';
import '../../static/conference_order.dart';
import '../../static/nfl_team_colors.dart';

// Source of truth is Terraform/scheduler-nfl-train-player-prop-model.tf's
// nfl_player_prop_stats map / scheduler-nba-train-player-prop-model.tf's
// nba_player_prop_stats map -- same duplication handler.py's own
// PLAYER_PROP_STATS accepts, since there's no model registry to read
// display labels from at runtime either. One shared map -- NFL's and
// NBA's stat keys never collide (different sports, different leaderboards
// maps), so there's nothing sport-conditional needed here.
const _statLabels = {
  'passing_yards': 'Passing Yards',
  'passing_touchdowns': 'Passing TDs',
  'rushing_yards': 'Rushing Yards',
  'rushing_touchdowns': 'Rushing TDs',
  'receiving_yards': 'Receiving Yards',
  'receiving_touchdowns': 'Receiving TDs',
  'defensive_sacks': 'Sacks',
  'points': 'Points',
  'rebounds': 'Rebounds',
  'assists': 'Assists',
  'steals': 'Steals',
  'blocks': 'Blocks',
  'three_pointers_made': '3-Pointers Made',
};

/// Buckets standings by division, preserving each team's relative order --
/// standings arrives already sorted by projected_wins descending (see
/// SeasonProjection's own doc comment), so each division's own bucket is
/// automatically best-to-worst with no separate sort needed here. filter,
/// when non-empty, keeps only divisions whose own name contains it
/// (case-insensitive) -- see _ConferenceFilterField.
List<MapEntry<String, List<TeamStanding>>> _groupByDivision(List<TeamStanding> standings, String filter) {
  final byDivision = <String, List<TeamStanding>>{};
  for (final team in standings) {
    byDivision.putIfAbsent(team.division ?? 'Other', () => []).add(team);
  }
  final needle = filter.trim().toLowerCase();
  final divisions = byDivision.keys.where((d) => needle.isEmpty || d.toLowerCase().contains(needle)).toList()
    ..sort(compareConferenceOrder);
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
  String _conferenceFilter = '';

  @override
  Widget build(BuildContext context) {
    final projection = ref.watch(seasonProjectionProvider(widget.sportId));

    return RefreshIndicator(
      onRefresh: () => ref.refresh(seasonProjectionProvider(widget.sportId).future),
      child: SingleChildScrollView(
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
            // Player Prop Leaders/NBA Cup/Bracket only exist as toggle
            // options when the backend actually sent that block --
            // NCAAFB's own season simulation is team-outcomes-only, no
            // player-level simulation and no in-season tournament (see
            // aws-lambdas/ncaafb/predict/season_projection.py's own
            // docstring), so `season.leaderboards`/`season.cup`/
            // `season.cupBracket` are always null there. `season.bracket`
            // is null for every sport without an elimination-bracket
            // concept (NCAA MBB/PGA/F1, not onboarded/not applicable) and,
            // same best-effort convention as `cup`, whenever building it
            // failed for a sport that normally has one.
            if (season.leaderboards != null || season.cup != null || season.bracket != null) ...[
              // Standings/player props/NBA Cup/Bracket each stand alone (a
              // toggle, not all stacked on one page) -- each is already a
              // tall multi-column section on its own, and stacking them
              // turns this into a very long scroll for no reason once a
              // viewer only wants one.
              // Horizontal-scroll, not a bare Row -- these labels
              // together don't fit a phone-width screen (see
              // sport_shell_page.dart's _TabToggle for the same pattern).
              SingleChildScrollView(
                scrollDirection: Axis.horizontal,
                child: Row(
                  children: [
                    _StatusToggle(
                      label: 'Standings & Playoff Odds',
                      selected: _tab == 'standings',
                      onTap: () => setState(() => _tab = 'standings'),
                    ),
                    if (season.leaderboards != null) ...[
                      const SizedBox(width: 8),
                      _StatusToggle(
                        label: 'Player Prop Leaders',
                        selected: _tab == 'props',
                        onTap: () => setState(() => _tab = 'props'),
                      ),
                    ],
                    if (season.cup != null) ...[
                      const SizedBox(width: 8),
                      _StatusToggle(
                        label: 'NBA Cup',
                        selected: _tab == 'cup',
                        onTap: () => setState(() => _tab = 'cup'),
                      ),
                    ],
                    if (season.bracket != null) ...[
                      const SizedBox(width: 8),
                      _StatusToggle(
                        label: 'Playoff Bracket',
                        selected: _tab == 'bracket',
                        onTap: () => setState(() => _tab = 'bracket'),
                      ),
                    ],
                    if (season.cupBracket != null) ...[
                      const SizedBox(width: 8),
                      _StatusToggle(
                        label: 'NBA Cup Bracket',
                        selected: _tab == 'cup_bracket',
                        onTap: () => setState(() => _tab = 'cup_bracket'),
                      ),
                    ],
                  ],
                ),
              ),
              const SizedBox(height: 20),
            ],
            if (_tab == 'props' && season.leaderboards != null)
              _Leaderboards(leaderboards: season.leaderboards)
            else if (_tab == 'cup' && season.cup != null)
              _CupSection(sport: season.sport, cup: season.cup!)
            else if (_tab == 'bracket' && season.bracket != null)
              _BracketSection(sport: season.sport, bracket: season.bracket!)
            else if (_tab == 'cup_bracket' && season.cupBracket != null)
              _BracketSection(sport: season.sport, bracket: season.cupBracket!)
            else ...[
              // Only shown once there's more than one conference/division
              // to filter -- a single-conference standings list (or NFL's
              // fixed 8 divisions, small enough to just scroll) has
              // nothing this would meaningfully narrow down.
              if (_groupByDivision(season.standings, '').length > 1) ...[
                ConferenceFilterField(
                  value: _conferenceFilter,
                  onChanged: (value) => setState(() => _conferenceFilter = value),
                ),
                const SizedBox(height: 16),
              ],
              // Fixed-width (capped by the viewport -- see
              // core/widgets/responsive.dart) division cards in a Wrap --
              // multiple divisions per row on a wide screen, same pattern
              // the leaderboard cards use, instead of one division per
              // full-width row.
              LayoutBuilder(
                builder: (context, constraints) {
                  // NCAAFB's extra RANK column (see _standingsColumns) needs
                  // more room than NFL's fixed 6-column table -- 480 was
                  // sized for NFL alone, and cramming a 7th column into the
                  // same width was clipping the RANK header.
                  final width = cardWidth(season.sport == 'ncaafb' ? 560 : 480, constraints.maxWidth);
                  final divisions = _groupByDivision(season.standings, _conferenceFilter);
                  if (divisions.isEmpty) {
                    return Text('No conferences match "$_conferenceFilter".', style: AppTextStyles.body(color: AppColors.inkSub));
                  }
                  return Wrap(
                    spacing: 20,
                    runSpacing: 20,
                    children: [
                      for (final division in divisions)
                        SizedBox(
                          width: width,
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Padding(
                                padding: const EdgeInsets.only(bottom: 8),
                                child: Text(division.key.toUpperCase(), style: AppTextStyles.microLabel(color: AppColors.cyan)),
                              ),
                              _StandingsTable(sport: season.sport, standings: division.value),
                            ],
                          ),
                        ),
                    ],
                  );
                },
              ),
            ],
          ],
        ),
        loading: () => const Center(child: Padding(padding: EdgeInsets.all(40), child: CircularProgressIndicator())),
        error: (error, _) =>
            Text('Couldn\'t load season projection: $error', style: AppTextStyles.body(color: AppColors.neg)),
      ),
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

// One list of (label, flex, cell) drives both the header row and every
// data row -- the two used to be built from independently-maintained
// label/flex arrays and a hardcoded Row, which is how NCAAFB ended up
// silently showing NFL's own column set (DIV%/PO%/SB%, no RANK) despite
// carrying genuinely different data underneath (conference_champion_
// probability, no division concept, a real ranking model NFL doesn't
// have -- see TeamStanding's own doc comments). Sport-conditional so the
// header label and the value it's actually showing can never drift apart
// again.
class _StandingsColumn {
  const _StandingsColumn(this.label, this.flex, this.cell);
  final String label;
  final int flex;
  final Widget Function(BuildContext context, String sport, TeamStanding team) cell;
}

List<_StandingsColumn> _standingsColumns(String sport) {
  final isNcaafb = sport == 'ncaafb';
  final isNba = sport == 'nba';
  return [
    // NCAAFB only -- NFL has no equivalent model/concept (see
    // TeamStanding.currentRank's own doc comment).
    if (isNcaafb)
      _StandingsColumn('RANK', 2, (context, sport, team) {
        final rank = team.currentRank;
        return Text(
          rank != null ? '#$rank' : '--',
          style: AppTextStyles.metricValue(color: AppColors.inkMute),
          textAlign: TextAlign.center,
          // A 3-digit rank (#100+) was wrapping onto a second line in
          // this narrow flex:1 column -- same hard backstop the header
          // row above already uses.
          maxLines: 1,
          softWrap: false,
          overflow: TextOverflow.ellipsis,
        );
      }),
    _StandingsColumn('TEAM', 3, (context, sport, team) {
      final info = teamDisplayFor(sport, team.teamId, team.abbreviation);
      return Row(
        children: [
          TeamColorDot(color: info.primary),
          if (info.primary != null) const SizedBox(width: 10),
          Flexible(
            child: Text(info.abbreviation, style: AppTextStyles.body(color: AppColors.ink), overflow: TextOverflow.ellipsis),
          ),
        ],
      );
    }),
    _StandingsColumn('PROJ', 2, (context, sport, team) => Text(
          // Rounded to whole games -- projectedWins/projectedLosses are
          // Monte Carlo averages (e.g. 10.6-6.4), not something that can
          // land on a real final record digit-for-digit.
          '${team.projectedWins.round()}-${team.projectedLosses.round()}',
          style: AppTextStyles.metricValue(color: AppColors.cyan),
          textAlign: TextAlign.center,
        )),
    _StandingsColumn('REC', 2, (context, sport, team) => Text(
          // Ties only appended when this team actually has one -- most
          // teams most seasons don't, and a universal "-0" reads as
          // noise on every other row. NBA never has one (team.ties is
          // always 0 there -- see TeamStanding's own doc comment), so
          // this already renders correctly for NBA with no branch needed.
          team.ties > 0 ? '${team.wins}-${team.losses}-${team.ties}' : '${team.wins}-${team.losses}',
          style: AppTextStyles.metricValue(),
          textAlign: TextAlign.center,
        )),
    // NBA swaps DIV% for PLAY-IN% -- the NBA hasn't tied any playoff-
    // seeding benefit to division titles since 2015-16 (see
    // season_simulation.py's own simulate_season docstring), so a DIV%
    // column would be misleading there; the play-in round is the real
    // extra tier NBA's own bracket has that NFL's/NCAAFB's don't.
    if (isNba)
      _StandingsColumn('PLAY-IN%', 2, (context, sport, team) => _PercentText(team.playInProbability ?? 0.0))
    else
      // "Division"/"Super Bowl" are NFL-specific words -- NCAAFB has no
      // division concept (conference championship stands in, see
      // TeamStanding.division's own doc comment) and plays for a national
      // championship, not a Super Bowl.
      _StandingsColumn(
        isNcaafb ? 'CONF%' : 'DIV%', 2, (context, sport, team) => _PercentText(team.divisionWinnerProbability),
      ),
    _StandingsColumn(
      isNba ? 'PLAYOFFS%' : (isNcaafb ? 'CFP%' : 'PO%'), 2, (context, sport, team) => _PercentText(team.playoffProbability),
    ),
    _StandingsColumn(
      isNba ? 'CHAMP%' : (isNcaafb ? 'NC%' : 'SB%'), 2, (context, sport, team) => _PercentText(team.championshipProbability),
    ),
  ];
}

class _StandingsTable extends StatelessWidget {
  const _StandingsTable({required this.sport, required this.standings});

  final String sport;
  final List<TeamStanding> standings;

  @override
  Widget build(BuildContext context) {
    if (standings.isEmpty) {
      return Text('No standings available yet.', style: AppTextStyles.body(color: AppColors.inkSub));
    }
    final columns = _standingsColumns(sport);
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
            child: _StandingsHeaderRow(columns: columns),
          ),
          for (final team in standings) ...[
            const Divider(height: 1, color: AppColors.border),
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 12),
              child: _StandingsRow(sport: sport, team: team, columns: columns),
            ),
          ],
        ],
      ),
    );
  }
}

class _StandingsHeaderRow extends StatelessWidget {
  const _StandingsHeaderRow({required this.columns});

  final List<_StandingsColumn> columns;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        for (var i = 0; i < columns.length; i++)
          Expanded(
            flex: columns[i].flex,
            child: Text(
              columns[i].label,
              style: AppTextStyles.microLabel(),
              textAlign: i == 0 ? TextAlign.start : TextAlign.center,
              // Short, fixed-abbreviation labels sized to comfortably fit
              // this column width -- maxLines/softWrap/ellipsis here are
              // a hard backstop against ever silently wrapping to a
              // second line, not the actual fit strategy.
              maxLines: 1,
              softWrap: false,
              overflow: TextOverflow.ellipsis,
            ),
          ),
      ],
    );
  }
}

class _StandingsRow extends StatelessWidget {
  const _StandingsRow({required this.sport, required this.team, required this.columns});

  final String sport;
  final TeamStanding team;
  final List<_StandingsColumn> columns;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        for (final column in columns) Expanded(flex: column.flex, child: column.cell(context, sport, team)),
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
    final entries = [
      for (final entry in _statLabels.entries)
        if (boards[entry.key]?.isNotEmpty ?? false) entry,
    ];
    if (entries.isEmpty) {
      return Text('No leaderboard data yet.', style: AppTextStyles.body(color: AppColors.inkSub));
    }
    return LayoutBuilder(
      builder: (context, constraints) {
        final width = cardWidth(320, constraints.maxWidth);
        return Wrap(
          spacing: 20,
          runSpacing: 20,
          children: [
            for (final entry in entries)
              SizedBox(width: width, child: _LeaderboardCard(label: entry.value, entries: boards[entry.key]!)),
          ],
        );
      },
    );
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
                  // Actual season-to-date total -> projected season-end
                  // total -- both, not just the projection, so this reads
                  // as "how far along" a leader is, not just where they'll
                  // land.
                  Text(
                    entries[i].currentTotal.toStringAsFixed(0),
                    style: AppTextStyles.metricValue(color: AppColors.inkMute),
                  ),
                  Text(' → ', style: AppTextStyles.microLabel(color: AppColors.inkMute)),
                  Text(
                    entries[i].projectedTotal.toStringAsFixed(0),
                    style: AppTextStyles.metricValue(color: AppColors.cyan),
                  ),
                ],
              ),
            ),
        ],
      ),
    );
  }
}

/// NBA Cup (in-season tournament) groups -- one card per group, sorted
/// alphabetically ("Eastern A" before "Eastern B" before "Western A",
/// etc). Same fixed-width-cards-in-a-Wrap layout as _Leaderboards above.
class _CupSection extends StatelessWidget {
  const _CupSection({required this.sport, required this.cup});

  final String sport;
  final CupProjection cup;

  @override
  Widget build(BuildContext context) {
    final groupNames = cup.groups.keys.toList()..sort();
    return LayoutBuilder(
      builder: (context, constraints) {
        final width = cardWidth(340, constraints.maxWidth);
        return Wrap(
          spacing: 20,
          runSpacing: 20,
          children: [
            for (final groupName in groupNames)
              SizedBox(
                width: width,
                child: _CupGroupCard(sport: sport, groupName: groupName, teams: cup.groups[groupName]!),
              ),
          ],
        );
      },
    );
  }
}

class _CupGroupCard extends StatelessWidget {
  const _CupGroupCard({required this.sport, required this.groupName, required this.teams});

  final String sport;
  final String groupName;
  final List<CupTeamStanding> teams;

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
          Text(groupName.toUpperCase(), style: AppTextStyles.microLabel(color: AppColors.cyan)),
          const SizedBox(height: 14),
          Row(
            children: [
              const Expanded(flex: 3, child: SizedBox()),
              Expanded(flex: 2, child: Text('REC', style: AppTextStyles.microLabel(), textAlign: TextAlign.center)),
              Expanded(flex: 2, child: Text('ADV%', style: AppTextStyles.microLabel(), textAlign: TextAlign.center)),
              Expanded(flex: 2, child: Text('CHAMP%', style: AppTextStyles.microLabel(), textAlign: TextAlign.center)),
            ],
          ),
          const Divider(height: 16, color: AppColors.border),
          for (final team in teams) ...[
            _CupTeamRow(sport: sport, team: team),
            const SizedBox(height: 8),
          ],
        ],
      ),
    );
  }
}

class _CupTeamRow extends StatelessWidget {
  const _CupTeamRow({required this.sport, required this.team});

  final String sport;
  final CupTeamStanding team;

  @override
  Widget build(BuildContext context) {
    final info = teamDisplayFor(sport, team.teamId, team.abbreviation);
    return Row(
      children: [
        Expanded(
          flex: 3,
          child: Row(
            children: [
              TeamColorDot(color: info.primary),
              if (info.primary != null) const SizedBox(width: 8),
              Flexible(
                child: Text(info.abbreviation, style: AppTextStyles.body(color: AppColors.ink), overflow: TextOverflow.ellipsis),
              ),
            ],
          ),
        ),
        Expanded(
          flex: 2,
          child: Text(
            '${team.groupWins}-${team.groupLosses}',
            style: AppTextStyles.metricValue(),
            textAlign: TextAlign.center,
          ),
        ),
        Expanded(flex: 2, child: _PercentText(team.knockoutProbability)),
        Expanded(flex: 2, child: _PercentText(team.championProbability)),
      ],
    );
  }
}

/// Playoff/Cup-knockout bracket -- a simple sequential row of round
/// columns per conference (horizontally scrollable), not a converging
/// dual-bracket graphic. Same "don't over-engineer the visual" approach
/// _CupSection's own plain group cards already take. Conference-split
/// sports (NFL/NBA -- bracket.conferences non-empty) get one round-row
/// per conference stacked vertically, then the cross-conference final as
/// its own small card; a flat-bracket sport (NCAAFB -- bracket.rounds
/// non-null) gets just one round-row, its own championship already the
/// last column in it.
class _BracketSection extends StatelessWidget {
  const _BracketSection({required this.sport, required this.bracket});

  final String sport;
  final BracketProjection bracket;

  @override
  Widget build(BuildContext context) {
    final flatRounds = bracket.rounds;
    if (flatRounds != null) {
      return _BracketRoundsRow(sport: sport, rounds: flatRounds, teamNames: bracket.teamNames);
    }

    final conferenceNames = bracket.conferences.keys.toList()..sort();
    final finalMatchup = bracket.finalMatchup;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (final conference in conferenceNames) ...[
          Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: Text(conference.toUpperCase(), style: AppTextStyles.microLabel(color: AppColors.cyan)),
          ),
          _BracketRoundsRow(sport: sport, rounds: bracket.conferences[conference]!, teamNames: bracket.teamNames),
          const SizedBox(height: 20),
        ],
        if (finalMatchup != null) ...[
          Text('CHAMPIONSHIP', style: AppTextStyles.microLabel(color: AppColors.cyan)),
          const SizedBox(height: 8),
          SizedBox(
            width: 240,
            child: _BracketMatchupCard(sport: sport, matchup: finalMatchup, teamNames: bracket.teamNames),
          ),
        ],
      ],
    );
  }
}

class _BracketRoundsRow extends StatelessWidget {
  const _BracketRoundsRow({required this.sport, required this.rounds, required this.teamNames});

  final String sport;
  final List<BracketRound> rounds;
  final Map<String, BracketTeamName> teamNames;

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          for (final round in rounds) ...[
            _BracketRoundColumn(sport: sport, round: round, teamNames: teamNames),
            const SizedBox(width: 16),
          ],
        ],
      ),
    );
  }
}

class _BracketRoundColumn extends StatelessWidget {
  const _BracketRoundColumn({required this.sport, required this.round, required this.teamNames});

  final String sport;
  final BracketRound round;
  final Map<String, BracketTeamName> teamNames;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: 220,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(round.round.toUpperCase(), style: AppTextStyles.microLabel()),
          const SizedBox(height: 8),
          for (final matchup in round.matchups) ...[
            _BracketMatchupCard(sport: sport, matchup: matchup, teamNames: teamNames),
            const SizedBox(height: 12),
          ],
        ],
      ),
    );
  }
}

class _BracketMatchupCard extends StatelessWidget {
  const _BracketMatchupCard({required this.sport, required this.matchup, required this.teamNames});

  final String sport;
  final BracketMatchup matchup;
  final Map<String, BracketTeamName> teamNames;

  @override
  Widget build(BuildContext context) {
    final winner = matchup.isFinal ? matchup.actualWinner : matchup.predictedWinner;
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(12),
        gradient: LinearGradient(begin: Alignment.topLeft, end: Alignment.bottomRight, colors: AppColors.surfaceGrad),
        border: Border.all(color: AppColors.borderRaised),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _BracketTeamRow(
            sport: sport,
            teamId: matchup.teamA,
            seed: matchup.seedA,
            teamNames: teamNames,
            isWinner: winner == matchup.teamA,
            score: matchup.isFinal ? matchup.actualHomeScore : null,
          ),
          const SizedBox(height: 4),
          _BracketTeamRow(
            sport: sport,
            teamId: matchup.teamB,
            seed: matchup.seedB,
            teamNames: teamNames,
            isWinner: winner == matchup.teamB,
            score: matchup.isFinal ? matchup.actualAwayScore : null,
          ),
          const SizedBox(height: 6),
          Text(_statusLabel(), style: AppTextStyles.microLabel(color: _statusColor()), overflow: TextOverflow.ellipsis),
        ],
      ),
    );
  }

  String _teamLabel(String teamId) {
    final info = teamNames[teamId];
    return teamDisplayFor(sport, teamId, info?.abbreviation).abbreviation;
  }

  // "Projected"/"Scheduled"/"Final" -- the 3-state design from
  // season_projection.py's own _resolve_matchup docstring, see
  // BracketMatchup's own doc comment.
  String _statusLabel() {
    final winnerLabel = matchup.predictedWinner != null ? _teamLabel(matchup.predictedWinner!) : null;
    switch (matchup.status) {
      case 'final':
        return 'FINAL';
      case 'scheduled':
        return matchup.winProbability != null && winnerLabel != null
            ? '${(matchup.winProbability! * 100).round()}% $winnerLabel'
            : 'PREDICTION PENDING';
      default:
        return matchup.winProbability != null && winnerLabel != null
            ? 'PROJECTED — ${(matchup.winProbability! * 100).round()}% $winnerLabel'
            : 'PROJECTED';
    }
  }

  Color _statusColor() {
    switch (matchup.status) {
      case 'final':
        return AppColors.cyan;
      case 'scheduled':
        return AppColors.ink;
      default:
        return AppColors.inkMute;
    }
  }
}

class _BracketTeamRow extends StatelessWidget {
  const _BracketTeamRow({
    required this.sport,
    required this.teamId,
    required this.seed,
    required this.teamNames,
    required this.isWinner,
    this.score,
  });

  final String sport;
  final String teamId;
  final int? seed;
  final Map<String, BracketTeamName> teamNames;
  final bool isWinner;
  final int? score;

  @override
  Widget build(BuildContext context) {
    final info = teamDisplayFor(sport, teamId, teamNames[teamId]?.abbreviation);
    return Row(
      children: [
        if (seed != null)
          SizedBox(width: 20, child: Text('$seed', style: AppTextStyles.microLabel(color: AppColors.inkMute))),
        TeamColorDot(color: info.primary),
        if (info.primary != null) const SizedBox(width: 8),
        Expanded(
          child: Text(
            info.abbreviation,
            style: AppTextStyles.body(color: isWinner ? AppColors.ink : AppColors.inkSub),
            overflow: TextOverflow.ellipsis,
          ),
        ),
        if (score != null)
          Text('$score', style: AppTextStyles.metricValue(color: isWinner ? AppColors.cyan : AppColors.inkMute)),
      ],
    );
  }
}

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

// Shared NFL/NBA player-prop stat display labels.
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

/// NBA standings group by conference only; every other sport groups by
/// division.
String _standingsGroupKey(String sport, String? division) {
  final raw = division ?? 'Other';
  if (sport != 'nba') return raw;
  final firstWord = raw.split(' ').first;
  return firstWord.isEmpty ? raw : firstWord;
}

/// Buckets standings by division (or NBA conference), preserving each
/// team's relative order. `filter`, when non-empty, keeps only groups whose
/// name contains it (case-insensitive).
List<MapEntry<String, List<TeamStanding>>> _groupByDivision(String sport, List<TeamStanding> standings, String filter) {
  final byDivision = <String, List<TeamStanding>>{};
  for (final team in standings) {
    byDivision.putIfAbsent(_standingsGroupKey(sport, team.division), () => []).add(team);
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
            // Toggle options only appear when the backend sent that block.
            if (season.leaderboards != null ||
                season.bracket != null ||
                season.cupBracket != null ||
                season.marchMadnessBracket != null ||
                season.conferenceBrackets != null) ...[
              // Horizontal-scroll Row -- the toggle labels don't fit a
              // phone-width screen.
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
                    if (season.marchMadnessBracket != null) ...[
                      const SizedBox(width: 8),
                      _StatusToggle(
                        label: 'March Madness',
                        selected: _tab == 'march_madness',
                        onTap: () => setState(() => _tab = 'march_madness'),
                      ),
                    ],
                    if (season.conferenceBrackets != null) ...[
                      const SizedBox(width: 8),
                      _StatusToggle(
                        label: 'Conference Brackets',
                        selected: _tab == 'conference_brackets',
                        onTap: () => setState(() => _tab = 'conference_brackets'),
                      ),
                    ],
                  ],
                ),
              ),
              const SizedBox(height: 20),
            ],
            if (_tab == 'props' && season.leaderboards != null)
              _Leaderboards(leaderboards: season.leaderboards)
            else if (_tab == 'bracket' && season.bracket != null)
              _BracketSection(sport: season.sport, bracket: season.bracket!)
            else if (_tab == 'cup_bracket' && season.cupBracket != null)
              _BracketSection(sport: season.sport, bracket: season.cupBracket!)
            else if (_tab == 'march_madness' && season.marchMadnessBracket != null)
              _MarchMadnessSection(sport: season.sport, bracket: season.marchMadnessBracket!)
            else if (_tab == 'conference_brackets' && season.conferenceBrackets != null)
              _ConferenceBracketsSection(sport: season.sport, conferenceBrackets: season.conferenceBrackets!)
            else ...[
              // Only shown when there's more than one conference/division
              // to filter.
              if (_groupByDivision(season.sport, season.standings, '').length > 1) ...[
                ConferenceFilterField(
                  value: _conferenceFilter,
                  onChanged: (value) => setState(() => _conferenceFilter = value),
                ),
                const SizedBox(height: 16),
              ],
              // Fixed-width division cards in a Wrap so multiple divisions
              // fit per row on a wide screen.
              LayoutBuilder(
                builder: (context, constraints) {
                  final width =
                      cardWidth(season.sport == 'ncaafb' || season.sport == 'ncaambb' ? 560 : 480, constraints.maxWidth);
                  final divisions = _groupByDivision(season.sport, season.standings, _conferenceFilter);
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
// data row, keeping each column's label and value in sync per sport.
class _StandingsColumn {
  const _StandingsColumn(this.label, this.flex, this.cell);
  final String label;
  final int flex;
  final Widget Function(BuildContext context, String sport, TeamStanding team) cell;
}

List<_StandingsColumn> _standingsColumns(String sport) {
  final isNcaafb = sport == 'ncaafb';
  final isNba = sport == 'nba';
  final isNcaambb = sport == 'ncaambb';
  return [
    // NCAAFB/NCAA MBB only -- both have a live national-ranking model.
    if (isNcaafb || isNcaambb)
      _StandingsColumn('RANK', 2, (context, sport, team) {
        final rank = team.currentRank;
        return Text(
          rank != null ? '#$rank' : '--',
          style: AppTextStyles.metricValue(color: AppColors.inkMute),
          textAlign: TextAlign.center,
          maxLines: 1,
          softWrap: false,
          overflow: TextOverflow.ellipsis,
        );
      }),
    _StandingsColumn('TEAM', 3, (context, sport, team) {
      final info = teamDisplayFor(sport, team.teamId, team.abbreviation, apiColor: team.color);
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
          // Rounded to whole games -- projectedWins/Losses are Monte Carlo
          // averages, not a real final record.
          '${team.projectedWins.round()}-${team.projectedLosses.round()}',
          style: AppTextStyles.metricValue(color: AppColors.cyan),
          textAlign: TextAlign.center,
        )),
    _StandingsColumn('REC', 2, (context, sport, team) => Text(
          // Ties only appended when non-zero (always 0 for NBA).
          team.ties > 0 ? '${team.wins}-${team.losses}-${team.ties}' : '${team.wins}-${team.losses}',
          style: AppTextStyles.metricValue(),
          textAlign: TextAlign.center,
        )),
    // NBA swaps DIV% for PLAY-IN%, its extra playoff-seeding tier.
    if (isNba)
      _StandingsColumn('PLAY-IN%', 2, (context, sport, team) => _PercentText(team.playInProbability ?? 0.0))
    else
      _StandingsColumn(
        isNcaafb || isNcaambb ? 'CONF%' : 'DIV%', 2, (context, sport, team) => _PercentText(team.divisionWinnerProbability),
      ),
    _StandingsColumn(
      isNba ? 'PLAYOFFS%' : (isNcaafb ? 'CFP%' : (isNcaambb ? 'NCAA%' : 'PO%')),
      2, (context, sport, team) => _PercentText(team.playoffProbability),
    ),
    _StandingsColumn(
      isNba || isNcaambb ? 'CHAMP%' : (isNcaafb ? 'NC%' : 'SB%'),
      2, (context, sport, team) => _PercentText(team.championshipProbability),
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
                  // Current total -> projected season-end total.
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

/// One row per conference tournament, collapsed by default -- avoids
/// rendering every conference's bracket at once. Tapping a row reveals its
/// own _BracketSection underneath.
class _ConferenceBracketsSection extends StatefulWidget {
  const _ConferenceBracketsSection({required this.sport, required this.conferenceBrackets});

  final String sport;
  final List<ConferenceBracket> conferenceBrackets;

  @override
  State<_ConferenceBracketsSection> createState() => _ConferenceBracketsSectionState();
}

class _ConferenceBracketsSectionState extends State<_ConferenceBracketsSection> {
  final Set<String> _expanded = {};

  @override
  Widget build(BuildContext context) {
    final sorted = [...widget.conferenceBrackets]
      ..sort((a, b) => compareConferenceOrder(a.conference, b.conference));
    return Column(
      children: [
        for (final entry in sorted) ...[
          _ConferenceBracketRow(
            sport: widget.sport,
            conferenceBracket: entry,
            expanded: _expanded.contains(entry.conference),
            onTap: () => setState(() {
              if (!_expanded.remove(entry.conference)) _expanded.add(entry.conference);
            }),
          ),
          const SizedBox(height: 12),
        ],
      ],
    );
  }
}

class _ConferenceBracketRow extends StatelessWidget {
  const _ConferenceBracketRow({
    required this.sport,
    required this.conferenceBracket,
    required this.expanded,
    required this.onTap,
  });

  final String sport;
  final ConferenceBracket conferenceBracket;
  final bool expanded;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(16),
        gradient: LinearGradient(begin: Alignment.topLeft, end: Alignment.bottomRight, colors: AppColors.surfaceGrad),
        border: Border.all(color: AppColors.borderRaised),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          InkWell(
            onTap: onTap,
            borderRadius: BorderRadius.circular(16),
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 16),
              child: Row(
                children: [
                  Expanded(
                    child: Text(
                      conferenceBracket.conference.toUpperCase(),
                      style: AppTextStyles.body(color: AppColors.ink),
                    ),
                  ),
                  Icon(expanded ? Icons.expand_less : Icons.expand_more, color: AppColors.inkMute),
                ],
              ),
            ),
          ),
          if (expanded)
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 0, 20, 20),
              child: _BracketSection(sport: sport, bracket: conferenceBracket.bracket),
            ),
        ],
      ),
    );
  }
}

/// Playoff/Cup-knockout bracket, rendered as a single converging tree
/// (_BracketTree). A flat-bracket sport (NCAAFB -- bracket.rounds
/// non-null) already has its championship as the last round. A
/// conference-split sport (NFL/NBA -- bracket.conferences non-empty) has
/// two conferences that converge into one shared championship card;
/// _computeConferenceBracketLayout lays each conference's tree out
/// independently and stacks them so their slot layouts can never collide.
class _BracketSection extends StatelessWidget {
  const _BracketSection({required this.sport, required this.bracket});

  final String sport;
  final BracketProjection bracket;

  @override
  Widget build(BuildContext context) {
    final flatRounds = bracket.rounds;
    if (flatRounds != null) {
      return _BracketTree(sport: sport, rounds: flatRounds, teamNames: bracket.teamNames, highlightFinalMatchup: true);
    }

    final conferenceNames = bracket.conferences.keys.toList()..sort();
    final finalMatchup = bracket.finalMatchup;
    if (conferenceNames.length != 2 || finalMatchup == null) {
      // Not the shape this combined layout assumes -- fall back to
      // independent per-conference trees.
      return Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          for (final conference in conferenceNames) ...[
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Text(conference.toUpperCase(), style: AppTextStyles.microLabel(color: AppColors.cyan)),
            ),
            _BracketTree(sport: sport, rounds: bracket.conferences[conference]!, teamNames: bracket.teamNames),
            const SizedBox(height: 20),
          ],
        ],
      );
    }

    final roundsA = bracket.conferences[conferenceNames[0]]!;
    final roundsB = bracket.conferences[conferenceNames[1]]!;
    final roundCount = roundsA.length < roundsB.length ? roundsA.length : roundsB.length;

    final combinedRounds = [
      for (var r = 0; r < roundCount; r++)
        BracketRound(round: roundsA[r].round, matchups: [...roundsA[r].matchups, ...roundsB[r].matchups]),
      BracketRound(round: sport == 'nfl' ? 'Super Bowl' : 'Championship', matchups: [finalMatchup]),
    ];

    final combined = _computeConferenceBracketLayout(roundsA, roundsB, finalMatchup);

    final conferenceLabels = [
      (name: conferenceNames[0], slot: 0.0),
      (name: conferenceNames[1], slot: combined.conferenceBOffset),
    ];

    return _BracketTree(
      sport: sport,
      rounds: combinedRounds,
      teamNames: bracket.teamNames,
      conferenceLabels: conferenceLabels,
      precomputedLayout: combined.layout,
      highlightFinalMatchup: true,
    );
  }
}

/// March Madness's own region-shaped layout -- 4 regions (not 2
/// conferences) each played down to a champion, those 4 champions meeting
/// at a Final Four (2 games), then a Championship. Generalizes
/// _computeConferenceBracketLayout's single region-pair-to-final merge to
/// a 2-stage merge (4 regions -> 2 Final Four winners -> 1 champion).
class _MarchMadnessSection extends StatelessWidget {
  const _MarchMadnessSection({required this.sport, required this.bracket});

  final String sport;
  final MarchMadnessBracket bracket;

  @override
  Widget build(BuildContext context) {
    final regionOrder = bracket.regions.keys.toList()..sort();
    final canGrid = regionOrder.length == 4 && bracket.finalFour.length == 2 && bracket.championship != null;

    // The grid path below places First Four cards itself, right next to
    // the Round of 64 slot each one feeds -- only the independent-tree
    // fallback (no fixed grid geometry to place them in) needs its own
    // separate, unpositioned section.
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (!canGrid) ...[
          if (bracket.firstFour.isNotEmpty) ...[
            _FirstFourSection(sport: sport, matchups: bracket.firstFour, bracket: bracket),
            const SizedBox(height: 20),
          ],
          // Not the shape this grid layout assumes -- fall back to
          // independent per-region trees.
          for (final region in regionOrder) ...[
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Text(region.toUpperCase(), style: AppTextStyles.microLabel(color: AppColors.cyan)),
            ),
            _BracketTree(sport: sport, rounds: bracket.regions[region]!.rounds, teamNames: bracket.teamNames),
            const SizedBox(height: 20),
          ],
        ] else
          _MarchMadnessGrid(sport: sport, bracket: bracket, regionOrder: regionOrder),
      ],
    );
  }
}

/// Not drawn as a connector line into the region grid below -- either
/// there's no fixed grid geometry to place it in (the independent-tree
/// fallback, whole regions.length != 4 shape), or (a grid IS present, but
/// this particular game's own predicted winner couldn't be matched into
/// any region's Round of 64 -- shouldn't happen for a self-consistent
/// payload, see _MarchMadnessGrid's own locateFirstFourDestination) --
/// either way this section is the fallback, unpositioned rendering; each
/// card names its resolved destination directly since it isn't drawn.
class _FirstFourSection extends StatelessWidget {
  const _FirstFourSection({required this.sport, required this.matchups, required this.bracket});

  final String sport;
  final List<BracketMatchup> matchups;
  final MarchMadnessBracket bracket;

  /// The region whose Round of 64 the matchup's predicted winner already
  /// occupies -- null only if the region data doesn't contain that team
  /// (shouldn't happen for a self-consistent payload).
  String? _destinationRegion(BracketMatchup matchup) {
    final winner = matchup.predictedWinner;
    if (winner == null) return null;
    for (final entry in bracket.regions.entries) {
      final roundOfSixtyFour = entry.value.rounds.isEmpty ? null : entry.value.rounds.first;
      if (roundOfSixtyFour == null) continue;
      if (roundOfSixtyFour.matchups.any((m) => m.teamA == winner || m.teamB == winner)) return entry.key;
    }
    return null;
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('FIRST FOUR', style: AppTextStyles.microLabel()),
        const SizedBox(height: 8),
        Wrap(
          spacing: 12,
          runSpacing: 12,
          children: [
            for (final matchup in matchups)
              SizedBox(
                width: _BracketTree._cardWidth,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    SizedBox(
                      height: _BracketTree._cardHeight,
                      child: _BracketMatchupCard(sport: sport, matchup: matchup, teamNames: bracket.teamNames, cardWidth: _BracketTree._cardWidth),
                    ),
                    const SizedBox(height: 4),
                    Builder(builder: (context) {
                      final destination = _destinationRegion(matchup);
                      return Text(
                        destination == null ? 'Winner advances to Round of 64' : 'Winner → ${destination.toUpperCase()} • Round of 64',
                        style: AppTextStyles.microLabel(color: AppColors.inkSub),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      );
                    }),
                  ],
                ),
              ),
          ],
        ),
      ],
    );
  }
}

/// The traditional 4-quadrant March Madness grid: regionOrder[0]/[1]
/// converge left-to-right into a Final Four card on the left half,
/// regionOrder[2]/[3] converge right-to-left (mirrored -- Round of 64 on
/// the outer/right edge, converging inward) into a Final Four card on the
/// right half, and both Final Four winners meet at a single Championship
/// card in the middle -- the same shape a printed bracket uses, unlike
/// _BracketTree's single left-to-right tree (which is right for every
/// other sport's bracket, none of which have 2 sides converging toward a
/// shared center).
///
/// Each half reuses _computeConferenceBracketLayout unchanged (it already
/// computes exactly "2 sources converge to 1 final matchup" -- the same
/// shape NFL/NBA's own conference-split bracket needs); only the mapping
/// from round index to horizontal position differs per half.
class _MarchMadnessGrid extends StatelessWidget {
  const _MarchMadnessGrid({required this.sport, required this.bracket, required this.regionOrder});

  final String sport;
  final MarchMadnessBracket bracket;
  final List<String> regionOrder;

  static const double _cardWidth = _BracketTree._cardWidth;
  static const double _cardHeight = _BracketTree._cardHeight;
  static const double _roundGap = _BracketTree._roundGap;
  static const double _verticalUnit = _BracketTree._verticalUnit;
  static const double _headerHeight = _BracketTree._headerHeight;
  static const double _labelClearance = _BracketTree._labelClearance;
  static const double _columnWidth = _cardWidth + _roundGap;
  static const double _championshipCardWidth = _BracketTree._championshipCardWidth;
  static const double _championshipCardHeight = _BracketTree._championshipCardHeight;

  @override
  Widget build(BuildContext context) {
    final leftRegionRounds = [bracket.regions[regionOrder[0]]!.rounds, bracket.regions[regionOrder[1]]!.rounds];
    final rightRegionRounds = [bracket.regions[regionOrder[2]]!.rounds, bracket.regions[regionOrder[3]]!.rounds];

    final left = _computeConferenceBracketLayout(leftRegionRounds[0], leftRegionRounds[1], bracket.finalFour[0]);
    final right = _computeConferenceBracketLayout(rightRegionRounds[0], rightRegionRounds[1], bracket.finalFour[1]);

    // Region rounds (Round of 64 .. Elite Eight) plus the Final Four round
    // _computeConferenceBracketLayout appends -- both halves share this
    // shape since every region is a fixed 16-team, no-bye field.
    final halfColumns = leftRegionRounds[0].length + 1;
    // First Four cards get their own outer column on whichever side they
    // feed (see _firstFourPlacements) -- reserved on both sides whenever
    // any First Four game exists, rather than computed per side, so the
    // grid's own column math doesn't depend on which specific regions
    // happen to draw a First Four game this run.
    final hasFirstFour = bracket.firstFour.isNotEmpty;
    final columnOffset = hasFirstFour ? 1 : 0;
    final championshipColumn = halfColumns + columnOffset;
    final firstFourLeftColumn = 0.0;
    final firstFourRightColumn = (halfColumns * 2 + columnOffset + 1).toDouble();
    double leftColumn(int round) => (round + columnOffset).toDouble();
    double rightColumn(int round) => (halfColumns * 2 - round + columnOffset).toDouble();
    double x(double column) => column * _columnWidth;
    double y(double slot) => slot * _verticalUnit;
    double yCenter(double slot) => y(slot) + _cardHeight / 2;

    var maxSlot = 0.0;
    for (final layout in [left.layout, right.layout]) {
      for (final roundSlots in layout.slots) {
        for (final slot in roundSlots) {
          if (slot > maxSlot) maxSlot = slot;
        }
      }
    }

    final leftFinalFourSlot = left.layout.slots[halfColumns - 1][0];
    final rightFinalFourSlot = right.layout.slots[halfColumns - 1][0];
    final championshipSlot = (leftFinalFourSlot + rightFinalFourSlot) / 2;

    // Round r's matchups, in the same left-then-right-region concatenation
    // order _computeConferenceBracketLayout used to build left/right.layout
    // -- index i into a round's slots always lines up with index i here.
    // Round halfColumns - 1 is the appended Final Four round, a single
    // matchup outside either region's own rounds.
    List<BracketMatchup> leftRoundMatchups(int r) =>
        r < halfColumns - 1 ? [...leftRegionRounds[0][r].matchups, ...leftRegionRounds[1][r].matchups] : [bracket.finalFour[0]];
    List<BracketMatchup> rightRoundMatchups(int r) =>
        r < halfColumns - 1 ? [...rightRegionRounds[0][r].matchups, ...rightRegionRounds[1][r].matchups] : [bracket.finalFour[1]];
    String roundLabel(List<BracketRound> regionRounds, int r) => r < halfColumns - 1 ? regionRounds[r].round : 'Final Four';

    // Each First Four game's predicted winner already holds a fixed,
    // known Round-of-64 slot (see season_projection.py's own
    // _march_madness_bracket_payload docstring -- region assignment is
    // seeded off that same predicted winner) -- find which side's
    // Round-of-64 list contains it and at what index, so the card can be
    // drawn at that row instead of in an unpositioned list. Null (should
    // not happen for a self-consistent payload) means this game is
    // skipped from the grid rather than crashing on a missing match.
    ({bool isLeft, int index})? locateFirstFourDestination(BracketMatchup matchup) {
      final winner = matchup.predictedWinner;
      if (winner == null) return null;
      final leftIndex = leftRoundMatchups(0).indexWhere((m) => m.teamA == winner || m.teamB == winner);
      if (leftIndex != -1) return (isLeft: true, index: leftIndex);
      final rightIndex = rightRoundMatchups(0).indexWhere((m) => m.teamA == winner || m.teamB == winner);
      if (rightIndex != -1) return (isLeft: false, index: rightIndex);
      return null;
    }

    final firstFourPlacements = [
      for (final matchup in bracket.firstFour)
        if (locateFirstFourDestination(matchup) case final destination?) (matchup: matchup, destination: destination),
    ];
    final hasLeftFirstFour = firstFourPlacements.any((p) => p.destination.isLeft);
    final hasRightFirstFour = firstFourPlacements.any((p) => !p.destination.isLeft);
    final unresolvedFirstFour = [
      for (final matchup in bracket.firstFour)
        if (locateFirstFourDestination(matchup) == null) matchup,
    ];

    // Centered on the same column/slot a same-size card would use, just
    // scaled up around that center point -- computed here (not just
    // where it's used to position the card widget below) since the
    // connector elbows entering it below need its own actual (shifted)
    // edges too, not the unshifted standard-card-width position the rest
    // of this grid's columns use.
    final championshipLeft = x(championshipColumn.toDouble()) - (_championshipCardWidth - _cardWidth) / 2;
    final championshipTop = y(championshipSlot) - (_championshipCardHeight - _cardHeight) / 2;

    final segments = <_GridSegment>[
      ..._sideSegments(left.layout.connections, leftColumn, yCenter, mirrored: false),
      ..._sideSegments(right.layout.connections, rightColumn, yCenter, mirrored: true),
      ..._elbow(
        Offset(x(leftColumn(halfColumns - 1)) + _cardWidth, yCenter(leftFinalFourSlot)),
        Offset(championshipLeft, yCenter(championshipSlot)),
        dashed: false,
      ),
      ..._elbow(
        Offset(x(rightColumn(halfColumns - 1)), yCenter(rightFinalFourSlot)),
        Offset(championshipLeft + _championshipCardWidth, yCenter(championshipSlot)),
        dashed: false,
      ),
      for (final placement in firstFourPlacements)
        ..._elbow(
          placement.destination.isLeft
              ? Offset(x(firstFourLeftColumn) + _cardWidth, yCenter(left.layout.slots[0][placement.destination.index]))
              : Offset(x(firstFourRightColumn), yCenter(right.layout.slots[0][placement.destination.index])),
          placement.destination.isLeft
              ? Offset(x(leftColumn(0)), yCenter(left.layout.slots[0][placement.destination.index]))
              : Offset(x(rightColumn(0)) + _cardWidth, yCenter(right.layout.slots[0][placement.destination.index])),
          dashed: false,
        ),
    ];

    final rightmostColumn = hasFirstFour ? firstFourRightColumn : rightColumn(0);
    final totalWidth = (rightmostColumn + 1) * _columnWidth - _roundGap;
    final totalHeight = maxSlot * _verticalUnit + _cardHeight;

    Widget regionLabel(String name, double column, double slot) => Positioned(
          left: x(column),
          top: y(slot) - _labelClearance,
          width: _cardWidth,
          child: Text(name.toUpperCase(), style: AppTextStyles.microLabel(color: AppColors.cyan), maxLines: 1, overflow: TextOverflow.ellipsis),
        );

    Widget roundHeader(String label, double column, {double width = _cardWidth}) => Positioned(
          left: x(column),
          width: width,
          child: Text(label.toUpperCase(), style: AppTextStyles.microLabel(), maxLines: 1, overflow: TextOverflow.ellipsis),
        );

    Widget card(BracketMatchup matchup, double column, double slot) => Positioned(
          left: x(column),
          top: y(slot),
          width: _cardWidth,
          height: _cardHeight,
          child: _BracketMatchupCard(sport: sport, matchup: matchup, teamNames: bracket.teamNames, cardWidth: _cardWidth),
        );

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (unresolvedFirstFour.isNotEmpty) ...[
          _FirstFourSection(sport: sport, matchups: unresolvedFirstFour, bracket: bracket),
          const SizedBox(height: 20),
        ],
        _HorizontalScrollableBracket(
          width: totalWidth,
          height: totalHeight + 2 * _headerHeight + 10,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              SizedBox(
                height: _headerHeight,
                child: Stack(
                  clipBehavior: Clip.none,
                  children: [
                    if (hasLeftFirstFour) roundHeader('First Four', firstFourLeftColumn),
                    for (var r = 0; r < halfColumns; r++) roundHeader(roundLabel(leftRegionRounds[0], r), leftColumn(r)),
                    for (var r = 0; r < halfColumns; r++) roundHeader(roundLabel(rightRegionRounds[0], r), rightColumn(r)),
                    if (hasRightFirstFour) roundHeader('First Four', firstFourRightColumn),
                    roundHeader('Championship', championshipColumn.toDouble(), width: _championshipCardWidth),
                  ],
                ),
              ),
              const SizedBox(height: 10 + _headerHeight),
              SizedBox(
                width: totalWidth,
                height: totalHeight,
                child: Stack(
                  clipBehavior: Clip.none,
                  children: [
                    regionLabel(regionOrder[0], leftColumn(0), 0),
                    regionLabel(regionOrder[1], leftColumn(0), left.conferenceBOffset),
                    regionLabel(regionOrder[2], rightColumn(0), 0),
                    regionLabel(regionOrder[3], rightColumn(0), right.conferenceBOffset),
                    Positioned.fill(child: CustomPaint(painter: _GridConnectorPainter(segments: segments, color: AppColors.inkSub))),
                    for (var r = 0; r < halfColumns; r++)
                      for (var i = 0; i < left.layout.slots[r].length; i++)
                        card(leftRoundMatchups(r)[i], leftColumn(r), left.layout.slots[r][i]),
                    for (var r = 0; r < halfColumns; r++)
                      for (var i = 0; i < right.layout.slots[r].length; i++)
                        card(rightRoundMatchups(r)[i], rightColumn(r), right.layout.slots[r][i]),
                    for (final placement in firstFourPlacements)
                      card(
                        placement.matchup,
                        placement.destination.isLeft ? firstFourLeftColumn : firstFourRightColumn,
                        placement.destination.isLeft
                            ? left.layout.slots[0][placement.destination.index]
                            : right.layout.slots[0][placement.destination.index],
                      ),
                    Positioned(
                      left: championshipLeft,
                      top: championshipTop,
                      width: _championshipCardWidth,
                      height: _championshipCardHeight,
                      child: _ChampionshipCard(sport: sport, matchup: bracket.championship!, teamNames: bracket.teamNames),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

/// The Championship card's own gradient border + glow -- see
/// _MarchMadnessGrid's own _championshipScale docstring for why this
/// slot specifically gets emphasis every other bracket card doesn't.
class _ChampionshipCard extends StatelessWidget {
  const _ChampionshipCard({required this.sport, required this.matchup, required this.teamNames});

  final String sport;
  final BracketMatchup matchup;
  final Map<String, BracketTeamName> teamNames;

  // Combined outer + inner Padding below, each side -- the actual width
  // _BracketMatchupCard renders at is _championshipCardWidth minus twice
  // this, not the full _championshipCardWidth. Passing the outer width
  // as its own cardWidth (as this used to) understates how much space
  // its FittedBox status line actually has, overflowing it -- a real bug
  // only visible in debug mode (the overflow's hazard-stripe rendering
  // and console warning are both debug-only).
  static const double _borderInset = 2 + 1.5;

  @override
  Widget build(BuildContext context) {
    // The outer box is unfilled -- only its boxShadow (the glow) and the
    // 2px gradient ring below it are visible. That ring is the "border":
    // _BracketMatchupCard's own card is meant to fully cover everything
    // inside it with its normal (same-as-every-other-card) background,
    // leaving only this thin band showing the gradient underneath -- but
    // that background is itself a translucent (~5-13% white) overlay,
    // designed to sit on the page's own solid dark background, not
    // directly on a vivid opaque gradient. Without an opaque backing
    // layer between them, the gradient showed straight through the
    // barely-there overlay, so the whole card read as solid gradient
    // fill instead of a normal card with a thin gradient border.
    return DecoratedBox(
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(16),
        boxShadow: [BoxShadow(color: AppColors.cyan.withValues(alpha: 0.25), blurRadius: 24, spreadRadius: 2)],
      ),
      child: Padding(
        padding: const EdgeInsets.all(2),
        child: DecoratedBox(
          decoration: BoxDecoration(borderRadius: BorderRadius.circular(14), gradient: AppColors.brandMark),
          child: Padding(
            padding: const EdgeInsets.all(1.5),
            child: DecoratedBox(
              decoration: BoxDecoration(borderRadius: BorderRadius.circular(13), color: AppColors.bg),
              child: ClipRRect(
                borderRadius: BorderRadius.circular(13),
                child: _BracketMatchupCard(
                  sport: sport,
                  matchup: matchup,
                  teamNames: teamNames,
                  cardWidth: _BracketTree._championshipCardWidth - 2 * _borderInset,
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// A resolved straight-line piece of a connector, in absolute pixel
/// coordinates -- unlike _BracketConnection (round/slot, resolved by
/// _BracketConnectorPainter using a single left-to-right mapping),
/// _MarchMadnessGrid's two halves use different round-to-column mappings
/// (one mirrored), so its segments are resolved to coordinates upfront
/// instead.
class _GridSegment {
  const _GridSegment(this.from, this.to, this.dashed);
  final Offset from;
  final Offset to;
  final bool dashed;
}

List<_GridSegment> _elbow(Offset from, Offset to, {required bool dashed}) {
  final midX = (from.dx + to.dx) / 2;
  return [
    _GridSegment(from, Offset(midX, from.dy), dashed),
    _GridSegment(Offset(midX, from.dy), Offset(midX, to.dy), dashed),
    _GridSegment(Offset(midX, to.dy), to, dashed),
  ];
}

/// Resolves one half's own _BracketConnection list (round/slot, as
/// _computeConferenceBracketLayout produced it) into absolute-coordinate
/// elbow segments. `mirrored: false` exits a source card's right edge and
/// enters a destination card's left edge (round index increases
/// left-to-right, same as _BracketConnectorPainter); `mirrored: true`
/// exits the source's left edge and enters the destination's right edge
/// (round index increases right-to-left, since the source round sits to
/// the destination round's right in the mirrored half).
List<_GridSegment> _sideSegments(
  List<_BracketConnection> connections,
  double Function(int round) column,
  double Function(double slot) yCenter, {
  required bool mirrored,
}) {
  final segments = <_GridSegment>[];
  for (final c in connections) {
    final fromX = column(c.fromRound) * _MarchMadnessGrid._columnWidth + (mirrored ? 0 : _MarchMadnessGrid._cardWidth);
    final toX = column(c.toRound) * _MarchMadnessGrid._columnWidth + (mirrored ? _MarchMadnessGrid._cardWidth : 0);
    segments.addAll(_elbow(Offset(fromX, yCenter(c.fromSlot)), Offset(toX, yCenter(c.toSlot)), dashed: c.isSkip));
  }
  return segments;
}

class _GridConnectorPainter extends CustomPainter {
  const _GridConnectorPainter({required this.segments, required this.color});
  final List<_GridSegment> segments;
  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = color
      ..strokeWidth = 1.5
      ..style = PaintingStyle.stroke;
    for (final segment in segments) {
      if (!segment.dashed) {
        canvas.drawLine(segment.from, segment.to, paint);
        continue;
      }
      const dashLength = 5.0;
      const gapLength = 4.0;
      final total = (segment.to - segment.from).distance;
      if (total == 0) continue;
      final direction = (segment.to - segment.from) / total;
      var walked = 0.0;
      while (walked < total) {
        final segmentEnd = (walked + dashLength).clamp(0.0, total);
        canvas.drawLine(segment.from + direction * walked, segment.from + direction * segmentEnd, paint);
        walked += dashLength + gapLength;
      }
    }
  }

  @override
  bool shouldRepaint(covariant _GridConnectorPainter oldDelegate) => oldDelegate.segments != segments;
}

/// One resolved bracket slot's position, in "slot units" (1 unit = one
/// vertical card-row). Round 0 gets sequential slots 0,1,2,...; each later
/// round's slot is the average of the earlier-round slot(s) its winner
/// traces back to (a bye uses its own round index instead). Matched by
/// winner team id rather than assuming round sizes halve, so a
/// non-halving round (NBA's play-in) still resolves correctly. Every round
/// then gets a dedup pass (see _computeBracketSlotLayout) so no two
/// matchups ever land on the same slot.
class _BracketSlotLayout {
  const _BracketSlotLayout(this.slots, this.connections);

  /// slots[roundIndex][matchupIndex] -> vertical slot position.
  final List<List<double>> slots;
  final List<_BracketConnection> connections;
}

class _BracketConnection {
  const _BracketConnection(this.fromRound, this.fromSlot, this.toRound, this.toSlot);
  final int fromRound;
  final double fromSlot;
  final int toRound;
  final double toSlot;

  /// True when the source is more than one round back (NBA's Play-In
  /// Elimination Game: a card's loser feeds the next round while its
  /// winner skips ahead two rounds). Styled differently in the painter so
  /// it doesn't read as a routing mistake.
  bool get isSkip => toRound - fromRound > 1;
}

_BracketSlotLayout _computeBracketSlotLayout(List<BracketRound> rounds) {
  final slots = <List<double>>[];
  final connections = <_BracketConnection>[];

  for (var r = 0; r < rounds.length; r++) {
    final matchups = rounds[r].matchups;
    if (r == 0) {
      slots.add([for (var i = 0; i < matchups.length; i++) i.toDouble()]);
      continue;
    }

    // A matchup's vertical position is based only on sources in the
    // immediately preceding round; a source found further back is drawn
    // as a connector (below) but doesn't drive placement, since averaging
    // it in can scramble a round's canonical seed order.
    final previousMatchups = rounds[r - 1].matchups;
    final previousSlots = slots[r - 1];
    final desired = List<double>.filled(matchups.length, 0);
    for (var i = 0; i < matchups.length; i++) {
      final matchup = matchups[i];
      final realSources = <double>[];
      final byeSources = <double>[];
      final currentSides = {matchup.teamA, matchup.teamB}..removeWhere((side) => side == null);
      for (var j = 0; j < previousMatchups.length; j++) {
        final previous = previousMatchups[j];
        final previousSides = {previous.teamA, previous.teamB}..removeWhere((side) => side == null);
        // A bye's own null side is never a "shared side" with another
        // bye's null side -- both were removed above, so this only
        // matches on a real team id appearing in both matchups.
        if (previousSides.intersection(currentSides).isNotEmpty) {
          (previous.teamA != null && previous.teamB != null ? realSources : byeSources).add(previousSlots[j]);
        }
      }
      // A bye source doesn't drive position when a real source is also
      // present -- the bye side never gets a card or a connector (see the
      // backward search below), so averaging its slot in would pull this
      // matchup off the real source's row for no visible reason, forcing
      // an otherwise-unnecessary dogleg into an already-straight
      // single-connector line. Only fall back to the bye's own slot when
      // it's the sole immediate source (no real game to align with).
      final immediateSources = realSources.isNotEmpty ? realSources : byeSources;
      // No traceable source in the immediately preceding round ("bye"
      // into this round) -- its own index stands in until the dedup pass
      // below places it.
      desired[i] = immediateSources.isEmpty
          ? i.toDouble()
          : immediateSources.reduce((a, b) => a + b) / immediateSources.length;
    }

    // Assign final slots by desired position (ties broken by original
    // index), pushing each one at least a full row past the slot just
    // assigned before it -- a full 1-row minimum gap, not just an
    // inequality check, since two desired values can be close enough to
    // overlap visually without being numerically equal.
    final order = List<int>.generate(matchups.length, (i) => i)
      ..sort((a, b) {
        final cmp = desired[a].compareTo(desired[b]);
        return cmp != 0 ? cmp : a.compareTo(b);
      });
    final roundSlots = List<double?>.filled(matchups.length, null);
    var lastAssigned = double.negativeInfinity;
    for (final i in order) {
      final candidate = desired[i] > lastAssigned + 1 ? desired[i] : lastAssigned + 1;
      roundSlots[i] = candidate;
      lastAssigned = candidate;
    }

    // Connector lines search back through every earlier round (nearest
    // first, matching either of a previous matchup's two participants),
    // independent of what drove position above -- draws the real source
    // even for a side that skips the immediately preceding round or that
    // only reappears as the loser of an earlier game.
    for (var i = 0; i < matchups.length; i++) {
      final matchup = matchups[i];
      for (final side in [matchup.teamA, matchup.teamB]) {
        if (side == null) continue; // A bye side has no earlier-round game to trace a connector back to.
        for (var back = r - 1; back >= 0; back--) {
          final foundIndex = rounds[back].matchups.indexWhere((m) => m.teamA == side || m.teamB == side);
          if (foundIndex != -1) {
            final source = rounds[back].matchups[foundIndex];
            // A bye source has no card rendered for it (see the card
            // loop below) -- nothing to draw a connector line from. The
            // team wasn't playing yet, it was awarded the round
            // automatically; this is where its own bracket path
            // actually starts, so search no further back either.
            if (source.teamA != null && source.teamB != null) {
              connections.add(_BracketConnection(back, slots[back][foundIndex], r, roundSlots[i]!));
            }
            break;
          }
        }
      }
    }

    slots.add(roundSlots.cast<double>());
  }

  return _BracketSlotLayout(slots, connections);
}

/// Merges two conferences' independently-computed bracket layouts into one
/// combined layout for the converging tree. Conference B is stacked as a
/// fixed band directly underneath conference A's tallest slot, so neither
/// conference's dedup pass ever sees the other's rows.
({_BracketSlotLayout layout, double conferenceBOffset}) _computeConferenceBracketLayout(
  List<BracketRound> roundsA,
  List<BracketRound> roundsB,
  BracketMatchup finalMatchup,
) {
  final layoutA = _computeBracketSlotLayout(roundsA);
  final layoutB = _computeBracketSlotLayout(roundsB);
  final roundCount = roundsA.length < roundsB.length ? roundsA.length : roundsB.length;

  var maxSlotA = 0.0;
  for (final roundSlots in layoutA.slots) {
    for (final slot in roundSlots) {
      if (slot > maxSlotA) maxSlotA = slot;
    }
  }
  final offset = maxSlotA + 1 + _BracketTree._labelSeamGapSlots;

  final slots = <List<double>>[];
  final connections = <_BracketConnection>[
    ...layoutA.connections,
    for (final c in layoutB.connections) _BracketConnection(c.fromRound, c.fromSlot + offset, c.toRound, c.toSlot + offset),
  ];
  for (var r = 0; r < roundCount; r++) {
    slots.add([...layoutA.slots[r], for (final slot in layoutB.slots[r]) slot + offset]);
  }

  // The championship's two sides are each conference's own last-round
  // winner -- trace which matchup produced it so the championship card
  // converges at the right height.
  double? sourceSlot(List<BracketRound> rounds, List<double> lastRoundSlots, double bandOffset) {
    final lastMatchups = rounds[roundCount - 1].matchups;
    for (var j = 0; j < lastMatchups.length; j++) {
      final previous = lastMatchups[j];
      final winner = previous.isFinal ? previous.actualWinner : previous.predictedWinner;
      if (winner != null && (winner == finalMatchup.teamA || winner == finalMatchup.teamB)) {
        return lastRoundSlots[j] + bandOffset;
      }
    }
    return null;
  }

  final finalSources = [
    sourceSlot(roundsA, layoutA.slots[roundCount - 1], 0),
    sourceSlot(roundsB, layoutB.slots[roundCount - 1], offset),
  ].whereType<double>().toList();
  final finalSlot = finalSources.isEmpty ? 0.0 : finalSources.reduce((a, b) => a + b) / finalSources.length;
  for (final source in finalSources) {
    connections.add(_BracketConnection(roundCount - 1, source, roundCount, finalSlot));
  }
  slots.add([finalSlot]);

  return (layout: _BracketSlotLayout(slots, connections), conferenceBOffset: offset);
}

/// Draws each round-to-round connector as a 3-segment elbow (horizontal out
/// of the source card, vertical to the target's row, horizontal into the
/// target card), positioned from _computeBracketSlotLayout's slot math.
///
/// A skip connection (see _BracketConnection.isSkip) is drawn dashed, in
/// `skipColor` instead of `color`, so a card feeding two different
/// destinations reads as intentional rather than a mistake.
class _BracketConnectorPainter extends CustomPainter {
  const _BracketConnectorPainter({
    required this.connections,
    required this.color,
    required this.skipColor,
    required this.cardWidth,
    required this.cardHeight,
    required this.roundGap,
    required this.verticalUnit,
    this.championshipRound,
    this.championshipShift = 0,
    this.championshipExtraGap = 0,
  });

  final List<_BracketConnection> connections;
  final Color color;
  final Color skipColor;
  final double cardWidth;
  final double cardHeight;
  final double roundGap;
  final double verticalUnit;

  /// The Championship card (see _BracketTree's own isChampionshipRound)
  /// is wider than every other card and centered on its own slot, so its
  /// actual left edge sits championshipShift px earlier than the
  /// standard round-index formula below would place it -- without this,
  /// a connection ending there stopped at the OLD (unshifted) x, which
  /// now lands inside the card's own enlarged bounds instead of at its
  /// edge, reading as the line just touching the card rather than
  /// leading cleanly into it.
  final int? championshipRound;
  final double championshipShift;

  /// _BracketTree's own _championshipEntryGap -- extra room before the
  /// Championship column on top of championshipShift already eating into
  /// the ordinary gap, so the connector legs leading into the card have
  /// enough length to actually read as a line rather than a stub.
  final double championshipExtraGap;

  double _x(int round) =>
      round * (cardWidth + roundGap) + (round == championshipRound ? championshipExtraGap - championshipShift : 0);
  double _y(double slot) => slot * verticalUnit + cardHeight / 2;

  void _drawSegment(Canvas canvas, Offset from, Offset to, Paint paint, {required bool dashed}) {
    if (!dashed) {
      canvas.drawLine(from, to, paint);
      return;
    }
    const dashLength = 5.0;
    const gapLength = 4.0;
    final total = (to - from).distance;
    if (total == 0) return;
    final direction = (to - from) / total;
    var walked = 0.0;
    while (walked < total) {
      final segmentEnd = (walked + dashLength).clamp(0.0, total);
      canvas.drawLine(from + direction * walked, from + direction * segmentEnd, paint);
      walked += dashLength + gapLength;
    }
  }

  @override
  void paint(Canvas canvas, Size size) {
    final normalPaint = Paint()
      ..color = color
      ..strokeWidth = 1.5
      ..style = PaintingStyle.stroke;
    final skipPaint = Paint()
      ..color = skipColor
      ..strokeWidth = 1.5
      ..style = PaintingStyle.stroke;
    for (final connection in connections) {
      final x1 = _x(connection.fromRound) + cardWidth;
      final y1 = _y(connection.fromSlot);
      final x2 = _x(connection.toRound);
      final y2 = _y(connection.toSlot);
      final paint = connection.isSkip ? skipPaint : normalPaint;
      // _elbow's own midpoint is (from.dx + to.dx) / 2 -- the real
      // midpoint, not x1 + roundGap / 2 (this painter's own old formula,
      // which assumed x2 - x1 always equals roundGap; true for every
      // ordinary round-to-round gap but not one entering the Championship
      // card, which sits championshipShift px closer than a standard
      // card would, see _x above). That old formula put the midpoint
      // PAST the card's own actual edge, collapsing the final leg
      // leading into the card down to just a couple px -- visually
      // indistinguishable from "the connector's vertical run just
      // touches the card," not a clean line leading to it.
      for (final segment in _elbow(Offset(x1, y1), Offset(x2, y2), dashed: connection.isSkip)) {
        _drawSegment(canvas, segment.from, segment.to, paint, dashed: segment.dashed);
      }
    }
  }

  @override
  bool shouldRepaint(covariant _BracketConnectorPainter oldDelegate) => oldDelegate.connections != connections;
}

/// Converging bracket tree -- rounds placed left to right, each matchup
/// card positioned by its computed slot, connector lines drawn underneath.
/// Horizontally scrollable; the outer page already scrolls vertically, so a
/// fixed-height inner Stack (sized to the widest round's card count) never
/// throws on overflow the way a Row/Column would.
class _BracketTree extends StatelessWidget {
  const _BracketTree({
    required this.sport,
    required this.rounds,
    required this.teamNames,
    this.conferenceLabels = const [],
    this.precomputedLayout,
    this.highlightFinalMatchup = false,
  });

  final String sport;
  final List<BracketRound> rounds;
  final Map<String, BracketTeamName> teamNames;

  /// Per-conference labels for a combined conference-split tree; each names
  /// the round-0 slot where that conference's band of matchups starts.
  final List<({String name, double slot})> conferenceLabels;

  /// True when `rounds`' own last round is genuinely this bracket's
  /// overall championship (Super Bowl/NBA Finals/National Championship/
  /// NBA Cup Championship) -- renders that one card via _ChampionshipCard
  /// instead of _BracketMatchupCard. False for a sub-bracket whose own
  /// last round is a real result but not THE championship (a single
  /// conference's own path when there's no shared final to combine into,
  /// or one March Madness region's own Elite Eight) -- set explicitly by
  /// each caller rather than inferred from round position, since
  /// "last round, one matchup" alone can't tell those cases apart.
  final bool highlightFinalMatchup;

  /// Precomputed layout for a combined conference-split tree (see
  /// _computeConferenceBracketLayout). Null for the flat (NCAAFB) and
  /// independent-tree fallback cases, which compute their own from
  /// `rounds` below.
  final _BracketSlotLayout? precomputedLayout;

  static const double _cardWidth = 220;
  static const double _cardHeight = 108;
  static const double _roundGap = 40;
  static const double _verticalUnit = 124;
  static const double _headerHeight = 20;

  // A conference/region label floats above the first card of its own
  // band (see conferenceLabels/_MarchMadnessGrid's own regionLabel) --
  // _headerHeight is the wrong amount to reserve for it: that constant
  // sizes the *fixed top round-name header row*, not this floating
  // label, and reusing it here left only (_verticalUnit - _cardHeight)
  // - _headerHeight = 124 - 108 - 20 = -4px of clearance -- the label
  // actually overlapped the previous row's card by 4px. This is sized to
  // comfortably fit one line of AppTextStyles.microLabel.
  static const double _labelClearance = 14;

  // Even with _labelClearance sized correctly, the ordinary row-to-row
  // gap (_verticalUnit - _cardHeight = 16px) only leaves ~2px of real
  // margin once the label's own height is subtracted from it -- visually
  // still reads as smushed against the card above. _computeConferenceBracketLayout's
  // own conferenceBOffset (where a 2nd conference/region's whole band
  // starts) adds this many extra slot-units on top of the normal 1-slot
  // gap, specifically at that one seam, for real breathing room -- not a
  // _verticalUnit change, which would space out every row in every
  // bracket in the app, not just this one seam.
  static const double _labelSeamGapSlots = 0.1;

  // The Championship/Super Bowl/National Championship card -- the single
  // matchup every other card in a bracket ultimately feeds -- renders
  // larger than every other card, with a gradient border and a soft
  // glow, so it reads as the destination at a glance. Shared by every
  // sport's _BracketTree (gated by highlightFinalMatchup above) and by
  // _MarchMadnessGrid's own separate Championship card, which isn't part
  // of any `rounds` list _BracketTree walks.
  static const double _championshipScale = 1.2;
  static const double _championshipCardWidth = _cardWidth * _championshipScale;
  static const double _championshipCardHeight = _cardHeight * _championshipScale;

  // Extra horizontal room before the Championship column, on top of the
  // ordinary _roundGap -- without it, the connector legs leading into
  // the card (see _BracketConnectorPainter's own championshipShift doc
  // comment) come out only ~9px long each: championshipShift (22px, half
  // the card's own width increase) already eats most of the ordinary 40px
  // gap, leaving little of it for an actually-visible line. This widens
  // the gap itself rather than shrinking the shift, so the card keeps
  // its own full emphasized size.
  static const double _championshipEntryGap = 28;

  @override
  Widget build(BuildContext context) {
    if (rounds.isEmpty) {
      return Text('Bracket not available yet.', style: AppTextStyles.body(color: AppColors.inkSub));
    }

    final layout = precomputedLayout ?? _computeBracketSlotLayout(rounds);
    var maxSlot = 0.0;
    for (final roundSlots in layout.slots) {
      for (final slot in roundSlots) {
        if (slot > maxSlot) maxSlot = slot;
      }
    }
    final isChampionshipRound = highlightFinalMatchup && rounds.last.matchups.length == 1;
    final championshipExtraWidth = isChampionshipRound ? (_championshipCardWidth - _cardWidth) / 2 : 0.0;
    final championshipExtraHeight = isChampionshipRound ? (_championshipCardHeight - _cardHeight) / 2 : 0.0;
    final championshipEntryGap = isChampionshipRound ? _championshipEntryGap : 0.0;
    final totalWidth =
        rounds.length * _cardWidth + (rounds.length - 1) * _roundGap + championshipExtraWidth + championshipEntryGap;
    final totalHeight = maxSlot * _verticalUnit + _cardHeight + championshipExtraHeight;
    final hasSkipConnection = layout.connections.any((c) => c.isSkip);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (hasSkipConnection) ...[
          Row(
            crossAxisAlignment: CrossAxisAlignment.center,
            children: [
              CustomPaint(size: const Size(28, 12), painter: _DashedLegendSwatchPainter(color: AppColors.violet)),
              const SizedBox(width: 8),
              Flexible(
                child: Text(
                  'Winner advances directly to a later round, skipping the Elimination Game',
                  style: AppTextStyles.microLabel(color: AppColors.inkSub),
                ),
              ),
            ],
          ),
          const SizedBox(height: 12),
        ],
        _HorizontalScrollableBracket(
          width: totalWidth,
          height: totalHeight + _headerHeight + (conferenceLabels.isEmpty ? 10 : 10 + _headerHeight),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              SizedBox(
                height: _headerHeight,
                child: Stack(
                  children: [
                    for (var r = 0; r < rounds.length; r++)
                      Positioned(
                        left: r * (_cardWidth + _roundGap),
                        width: _cardWidth,
                        child: Text(
                          rounds[r].round.toUpperCase(),
                          style: AppTextStyles.microLabel(),
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                  ],
                ),
              ),
              SizedBox(height: conferenceLabels.isEmpty ? 10 : 10 + _headerHeight),
              SizedBox(
                width: totalWidth,
                height: totalHeight,
                child: Stack(
                  // conferenceLabels can sit above the first card's top (a
                  // negative `top`); Stack clips by default, which would
                  // silently drop that label.
                  clipBehavior: Clip.none,
                  children: [
                    for (final label in conferenceLabels)
                      Positioned(
                        left: 0,
                        top: label.slot * _verticalUnit - _labelClearance,
                        width: _cardWidth,
                        child: Text(
                          label.name.toUpperCase(),
                          style: AppTextStyles.microLabel(color: AppColors.cyan),
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                    Positioned.fill(
                      child: CustomPaint(
                        painter: _BracketConnectorPainter(
                          connections: layout.connections,
                          color: AppColors.inkSub,
                          skipColor: AppColors.violet,
                          cardWidth: _cardWidth,
                          cardHeight: _cardHeight,
                          roundGap: _roundGap,
                          verticalUnit: _verticalUnit,
                          championshipRound: isChampionshipRound ? rounds.length - 1 : null,
                          championshipShift: championshipExtraWidth,
                          championshipExtraGap: championshipEntryGap,
                        ),
                      ),
                    ),
                    // A bye matchup (either side null -- see BracketMatchup's
                    // own doc comment) gets no card at all: the team it
                    // awarded simply appears already present in its own
                    // next real game, same as _computeBracketSlotLayout's
                    // connector search already skips drawing a line back
                    // to a bye (nothing to connect to).
                    for (var r = 0; r < rounds.length; r++)
                      for (var i = 0; i < rounds[r].matchups.length; i++)
                        if (rounds[r].matchups[i].teamA != null && rounds[r].matchups[i].teamB != null)
                          if (isChampionshipRound && r == rounds.length - 1 && i == 0)
                            Positioned(
                              left: r * (_cardWidth + _roundGap) + championshipEntryGap - championshipExtraWidth,
                              top: layout.slots[r][i] * _verticalUnit - championshipExtraHeight,
                              width: _championshipCardWidth,
                              height: _championshipCardHeight,
                              child: _ChampionshipCard(sport: sport, matchup: rounds[r].matchups[i], teamNames: teamNames),
                            )
                          else
                            Positioned(
                              left: r * (_cardWidth + _roundGap),
                              top: layout.slots[r][i] * _verticalUnit,
                              width: _cardWidth,
                              height: _cardHeight,
                              child: _BracketMatchupCard(
                                sport: sport,
                                matchup: rounds[r].matchups[i],
                                teamNames: teamNames,
                                cardWidth: _cardWidth,
                              ),
                            ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

/// One parent widget holding both the scrollable bracket and its
/// horizontal scrollbar, with the bar genuinely pinned to this widget's
/// own bottom edge -- fixed there regardless of how far the bracket is
/// scrolled vertically, rather than a Scrollbar's usual behavior of
/// sitting at the bottom edge of its own (possibly several-thousand-px-
/// tall) content. A card can legitimately scroll behind the bar as a
/// result (the bar is a real overlay, drawn on top, not part of the
/// vertical scroll flow) -- accepted trade-off for keeping the bar
/// reachable without first scrolling the whole bracket down to its own
/// natural end.
///
/// Only kicks in once `height` (the bracket's real content height)
/// actually exceeds a viewport-relative cap: a bracket that already fits
/// in one screenful renders as a plain Column (content, then the bar
/// right below it) with no inner scroll region or overlay at all, since
/// there's nothing for a card to scroll behind in the first place.
class _HorizontalScrollableBracket extends StatefulWidget {
  const _HorizontalScrollableBracket({required this.width, required this.height, required this.child});

  final double width;
  final double height;
  final Widget child;

  @override
  State<_HorizontalScrollableBracket> createState() => _HorizontalScrollableBracketState();
}

class _HorizontalScrollableBracketState extends State<_HorizontalScrollableBracket> {
  // Real, interactive horizontal scroll of the bracket content itself.
  final _contentController = ScrollController();
  // The overlay bar's own horizontal scroll -- a second real Scrollable
  // (not just a repaint of _contentController's position) so its own
  // Scrollbar thumb is directly draggable, kept in sync with
  // _contentController by a plain bidirectional listener (sharing one
  // ScrollController between two simultaneously-visible Scrollables does
  // NOT sync drag gestures on its own -- only that controller's own
  // jumpTo/animateTo calls reach every attached position).
  final _barController = ScrollController();
  final _verticalController = ScrollController();
  bool _syncingHorizontal = false;

  // A cap, not a fixed size -- a bracket shorter than this (most
  // conference tournaments, NFL/NBA's own non-March-Madness brackets)
  // renders with no inner scroll region/overlay at all (see build()).
  static const double _maxPaneHeightFraction = 0.65;
  static const double _minPaneHeight = 360;
  static const double _maxPaneHeight = 720;
  static const double _barHeight = 14;
  static const double _barGap = 8;

  @override
  void initState() {
    super.initState();
    _contentController.addListener(() => _syncHorizontal(from: _contentController, to: _barController));
    _barController.addListener(() => _syncHorizontal(from: _barController, to: _contentController));
  }

  void _syncHorizontal({required ScrollController from, required ScrollController to}) {
    if (_syncingHorizontal || !from.hasClients || !to.hasClients) return;
    final target = from.offset.clamp(0.0, to.position.maxScrollExtent);
    if (target == to.offset) return;
    _syncingHorizontal = true;
    to.jumpTo(target);
    _syncingHorizontal = false;
  }

  @override
  void dispose() {
    _contentController.dispose();
    _barController.dispose();
    _verticalController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final content = SingleChildScrollView(
      controller: _contentController,
      scrollDirection: Axis.horizontal,
      child: SizedBox(width: widget.width, height: widget.height, child: widget.child),
    );

    final bar = Scrollbar(
      controller: _barController,
      thumbVisibility: true,
      child: SingleChildScrollView(
        controller: _barController,
        scrollDirection: Axis.horizontal,
        child: SizedBox(width: widget.width, height: _barHeight),
      ),
    );

    final viewportCap = (MediaQuery.sizeOf(context).height * _maxPaneHeightFraction).clamp(_minPaneHeight, _maxPaneHeight);
    if (widget.height <= viewportCap) {
      return Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [content, const SizedBox(height: _barGap), bar],
      );
    }

    return SizedBox(
      height: viewportCap,
      child: Stack(
        children: [
          Positioned.fill(
            bottom: _barHeight + _barGap,
            child: SingleChildScrollView(controller: _verticalController, child: content),
          ),
          Positioned(left: 0, right: 0, bottom: 0, child: bar),
        ],
      ),
    );
  }
}

/// Small dashed-line swatch for the skip-connection legend, reusing the
/// connector painter's dash geometry so the sample matches the real lines.
class _DashedLegendSwatchPainter extends CustomPainter {
  const _DashedLegendSwatchPainter({required this.color});
  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = color
      ..strokeWidth = 1.5
      ..style = PaintingStyle.stroke;
    const dashLength = 5.0;
    const gapLength = 4.0;
    final y = size.height / 2;
    var x = 0.0;
    while (x < size.width) {
      final segmentEnd = (x + dashLength).clamp(0.0, size.width);
      canvas.drawLine(Offset(x, y), Offset(segmentEnd, y), paint);
      x += dashLength + gapLength;
    }
  }

  @override
  bool shouldRepaint(covariant _DashedLegendSwatchPainter oldDelegate) => oldDelegate.color != color;
}

class _BracketMatchupCard extends StatelessWidget {
  const _BracketMatchupCard({required this.sport, required this.matchup, required this.teamNames, required this.cardWidth});

  final String sport;
  final BracketMatchup matchup;
  final Map<String, BracketTeamName> teamNames;

  /// Threaded from _BracketTree's _cardWidth -- gives the status line a
  /// real width to wrap against inside the FittedBox below.
  final double cardWidth;

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
            // A series' running win count takes priority over a single
            // game's score, shown throughout (including 0-0).
            score: matchup.isSeries ? matchup.winsA : (matchup.isFinal ? matchup.actualHomeScore : null),
          ),
          const SizedBox(height: 4),
          _BracketTeamRow(
            sport: sport,
            teamId: matchup.teamB,
            seed: matchup.seedB,
            teamNames: teamNames,
            isWinner: winner == matchup.teamB,
            score: matchup.isSeries ? matchup.winsB : (matchup.isFinal ? matchup.actualAwayScore : null),
          ),
          const SizedBox(height: 6),
          // Shrinks the wrapped status text to fit whatever vertical space
          // remains, so it can't overflow the fixed-height card.
          Expanded(
            child: FittedBox(
              fit: BoxFit.scaleDown,
              alignment: Alignment.topLeft,
              child: SizedBox(
                width: cardWidth - 24, // Container's own 12px padding each side
                child: Text(
                  _statusLabel(),
                  style: AppTextStyles.microLabel(color: _statusColor()),
                  maxLines: 3,
                  softWrap: true,
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }

  String _teamLabel(String teamId) {
    final info = teamNames[teamId];
    return teamDisplayFor(sport, teamId, info?.abbreviation).abbreviation;
  }

  // "Projected"/"Scheduled"/"Final". A series matchup (isSeries) folds in
  // its running win-loss record.
  String _statusLabel() {
    final winnerLabel = matchup.predictedWinner != null ? _teamLabel(matchup.predictedWinner!) : null;
    if (matchup.isSeries) {
      // The live win-loss record (winsA/winsB) is shown per-team by this
      // card's own team rows, so it isn't repeated here. Only the
      // predicted final record (winner-first, e.g. "4-2") appears on the
      // status line.
      final predictedRecord = matchup.predictedWinsA != null && matchup.predictedWinsB != null
          ? (matchup.predictedWinner == matchup.teamA
              ? '${matchup.predictedWinsA}-${matchup.predictedWinsB}'
              : '${matchup.predictedWinsB}-${matchup.predictedWinsA}')
          : null;
      switch (matchup.status) {
        case 'final':
          final winnerName = matchup.actualWinner != null ? _teamLabel(matchup.actualWinner!) : null;
          return winnerName != null ? '$winnerName WINS SERIES' : 'SERIES FINAL';
        case 'scheduled':
          if (matchup.winProbability == null || winnerLabel == null) return 'PREDICTION PENDING';
          final probability = '${(matchup.winProbability! * 100).round()}%';
          return predictedRecord != null ? '$winnerLabel $predictedRecord $probability' : '$probability $winnerLabel';
        default:
          if (matchup.winProbability == null || winnerLabel == null) return 'PROJECTED';
          final probability = '${(matchup.winProbability! * 100).round()}%';
          return predictedRecord != null
              ? 'PROJECTED — $winnerLabel $predictedRecord $probability'
              : 'PROJECTED — $probability $winnerLabel';
      }
    }
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
  // Null for the side that got a bye into this round -- see
  // BracketMatchup's own doc comment.
  final String? teamId;
  final int? seed;
  final Map<String, BracketTeamName> teamNames;
  final bool isWinner;
  final int? score;

  @override
  Widget build(BuildContext context) {
    final id = teamId;
    if (id == null) {
      return Row(
        children: [
          if (seed != null)
            SizedBox(width: 20, child: Text('$seed', style: AppTextStyles.microLabel(color: AppColors.inkMute))),
          Expanded(
            child: Text('BYE', style: AppTextStyles.body(color: AppColors.inkMute), overflow: TextOverflow.ellipsis),
          ),
        ],
      );
    }
    final info = teamDisplayFor(sport, id, teamNames[id]?.abbreviation, apiColor: teamNames[id]?.color);
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

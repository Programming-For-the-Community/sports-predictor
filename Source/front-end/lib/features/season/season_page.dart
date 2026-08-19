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

/// NBA's own `division` value is the fine-grained "Eastern Atlantic"/
/// "Western Pacific"/etc (library.features.nba_teams.TEAM_DIVISIONS,
/// unchanged -- still the real division a team belongs to) -- but real
/// NBA standings pages are conventionally shown by conference only (2
/// groups), not sub-divided by division the way NFL's AFC East/NFC West
/// etc. conventionally are. This takes just the conference-prefix word
/// off NBA's division string for grouping purposes only; every other
/// sport's grouping is unaffected.
String _standingsGroupKey(String sport, String? division) {
  final raw = division ?? 'Other';
  if (sport != 'nba') return raw;
  final firstWord = raw.split(' ').first;
  return firstWord.isEmpty ? raw : firstWord;
}

/// Buckets standings by division (or, for NBA, by conference -- see
/// _standingsGroupKey), preserving each team's relative order --
/// standings arrives already sorted by projected_wins descending (see
/// SeasonProjection's own doc comment), so each group's own bucket is
/// automatically best-to-worst with no separate sort needed here. filter,
/// when non-empty, keeps only groups whose own name contains it
/// (case-insensitive) -- see _ConferenceFilterField.
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
            // Player Prop Leaders/Bracket/NBA Cup Bracket only exist as
            // toggle options when the backend actually sent that block --
            // NCAAFB's own season simulation is team-outcomes-only, no
            // player-level simulation and no in-season tournament (see
            // aws-lambdas/ncaafb/predict/season_projection.py's own
            // docstring), so `season.leaderboards`/`season.cupBracket` are
            // always null there. `season.bracket` is null for every sport
            // without an elimination-bracket concept (NCAA MBB/PGA/F1, not
            // onboarded/not applicable) and, same best-effort convention,
            // whenever building it failed for a sport that normally has
            // one. `season.cup` (NBA Cup group standings) still exists on
            // the payload -- `project_cup_knockout_bracket`'s own seeding
            // still needs the group win/loss records it carries -- but no
            // longer has a tab of its own; the Cup's own bracket tab
            // (`cupBracket`) already shows the same tournament without a
            // separate group-standings view alongside it.
            if (season.leaderboards != null || season.bracket != null || season.cupBracket != null) ...[
              // Standings/player props/Bracket/NBA Cup Bracket each stand
              // alone (a toggle, not all stacked on one page) -- each is
              // already a tall multi-column section on its own, and
              // stacking them turns this into a very long scroll for no
              // reason once a viewer only wants one.
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
            else if (_tab == 'bracket' && season.bracket != null)
              _BracketSection(sport: season.sport, bracket: season.bracket!)
            else if (_tab == 'cup_bracket' && season.cupBracket != null)
              _BracketSection(sport: season.sport, bracket: season.cupBracket!)
            else ...[
              // Only shown once there's more than one conference/division
              // to filter -- a single-conference standings list (or NFL's
              // fixed 8 divisions, small enough to just scroll) has
              // nothing this would meaningfully narrow down.
              if (_groupByDivision(season.sport, season.standings, '').length > 1) ...[
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
        for (var i = 0; i < columns.length; i++) ...[
          if (i > 0) const SizedBox(width: 6),
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

/// Playoff/Cup-knockout bracket -- a single converging tree (_BracketTree).
/// A flat-bracket sport (NCAAFB -- bracket.rounds non-null) already has its
/// own championship as the last round. A conference-split sport (NFL/NBA
/// -- bracket.conferences non-empty) has two conferences that must
/// visually converge into one shared championship card, not two
/// independent trees with a disconnected final floating below them.
/// _computeConferenceBracketLayout lays each conference's own tree out
/// independently, then stacks conference B underneath conference A as a
/// fixed vertical band -- concatenating both conferences' matchups into
/// one list and running _computeBracketSlotLayout on the merged list
/// directly (the previous approach) let a bye's collision-avoidance in
/// one conference land on a slot that numerically coincides with the
/// other conference's own traced slot, silently swapping two of that
/// conference's own matchups out of their normal (1v8)/(4v5)/(3v6)/(2v7)
/// seed order and producing crossed, multi-elbow connector lines -- a
/// real bug reported against NBA's own bracket. Two isolated per-
/// conference layouts can never collide this way.
class _BracketSection extends StatelessWidget {
  const _BracketSection({required this.sport, required this.bracket});

  final String sport;
  final BracketProjection bracket;

  @override
  Widget build(BuildContext context) {
    final flatRounds = bracket.rounds;
    if (flatRounds != null) {
      return _BracketTree(sport: sport, rounds: flatRounds, teamNames: bracket.teamNames);
    }

    final conferenceNames = bracket.conferences.keys.toList()..sort();
    final finalMatchup = bracket.finalMatchup;
    if (conferenceNames.length != 2 || finalMatchup == null) {
      // Not the shape this combined layout assumes -- fall back to
      // independent per-conference trees rather than guessing.
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
    );
  }
}

/// One resolved bracket slot's position, in "slot units" (1 unit = one
/// vertical card-row) -- round 0 gets simple sequential slots 0,1,2,...;
/// every later round's own slot starts as the average of whichever
/// earlier-round slot(s) its own winner traces back to (a bye -- a team
/// with no prior-round game at all, e.g. NFL's #1 seed skipping Wild
/// Card -- has no source slots, so its own round index stands in
/// instead). Matched generically by winner team id (predicted or actual,
/// see BracketMatchup.isFinal) rather than assuming round sizes always
/// halve, so a play-in-style round (NBA) that doesn't cleanly halve still
/// resolves correctly -- nothing here is sport-specific. Every round then
/// gets a single dedup pass (see _computeBracketSlotLayout) so no two
/// matchups -- traced or bye -- ever land on the same slot.
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

  /// True when this connection's source is more than one round back --
  /// NBA's Play-In "Elimination Game" is the only round shape in this
  /// project where a single source card has two DIFFERENT real
  /// destinations (its loser feeds the very next round, its winner skips
  /// that round entirely and reappears two rounds later, straight in the
  /// Quarterfinals -- see _computeBracketSlotLayout's own connector-
  /// search comment). Styled differently in the painter so a card that
  /// feeds two different places doesn't read as a routing mistake.
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

    // A matchup's own VERTICAL POSITION is based only on sources found in
    // the IMMEDIATELY preceding round -- a source found further back (see
    // the connector search below) is deliberately excluded here, even
    // though it's real. Averaging it in can pull a card toward an
    // unrelated bye's own fallback position and scramble a round's
    // otherwise-canonical seed order: NBA's Play-In "Elimination Game"
    // splits Play-In into two rounds, so one Quarterfinal side's real
    // source sits two rounds back (the OTHER Play-In game's winner skips
    // the Elimination round entirely) -- confirmed via hand-trace that
    // using it for position, not just the connector line, swaps two
    // Quarterfinal cards out of their (1v8)/(4v5)/(3v6)/(2v7) order. A
    // matchup whose only source is further back is positioned exactly
    // like a bye instead -- its own list index -- and the actual
    // connector to that further-back source is still drawn correctly
    // (see below), it just doesn't drive placement.
    final previousMatchups = rounds[r - 1].matchups;
    final previousSlots = slots[r - 1];
    final desired = List<double>.filled(matchups.length, 0);
    for (var i = 0; i < matchups.length; i++) {
      final matchup = matchups[i];
      final immediateSources = <double>[];
      for (var j = 0; j < previousMatchups.length; j++) {
        final previous = previousMatchups[j];
        if (previous.teamA == matchup.teamA || previous.teamA == matchup.teamB ||
            previous.teamB == matchup.teamA || previous.teamB == matchup.teamB) {
          immediateSources.add(previousSlots[j]);
        }
      }
      // A matchup with no traceable source in the immediately preceding
      // round ("bye" into this round -- e.g. NBA's direct #4/#5 seeds,
      // which skip Play-In entirely) has no average to compute; its own
      // index stands in as its desired slot until the dedup pass below
      // places it.
      desired[i] = immediateSources.isEmpty
          ? i.toDouble()
          : immediateSources.reduce((a, b) => a + b) / immediateSources.length;
    }

    // Assign final slots by desired position (ties broken by original
    // index), walking through in ascending order and pushing each one at
    // least a full row past whichever slot was just assigned before it.
    // An EXACT duplicate isn't the only collision to guard against -- two
    // desired values that are merely close (e.g. Play-In Elimination's
    // own single-game average landing on 0.5, half a row above a bye's
    // integer fallback at 1.0) are numerically distinct but still overlap
    // visually, since a card is taller than half a row (a real bug: NBA's
    // own 1v8/4v5 Quarterfinal cards, at slots 0.5 and 1.0, physically
    // overlapped despite never computing the same slot). Enforcing a full
    // 1-row minimum gap between every consecutive pair, not just an
    // inequality check, catches both cases the same way.
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

    // Connector lines search back through EVERY earlier round (nearest
    // first, matching either of a previous matchup's two participants,
    // not just its resolved winner), independent of what drove position
    // above -- draws the real source even for a side that skips the
    // immediately preceding round entirely (NBA's Play-In winner
    // advancing straight to the Quarterfinals) or that only reappears as
    // the LOSER of an earlier game (NBA's Play-In Elimination Game). A
    // normal single-elimination loser is never placed into any later
    // matchup, so the "either participant" check is a no-op everywhere
    // except Play-In's own two-stage relationship.
    for (var i = 0; i < matchups.length; i++) {
      final matchup = matchups[i];
      for (final side in [matchup.teamA, matchup.teamB]) {
        for (var back = r - 1; back >= 0; back--) {
          final foundIndex = rounds[back].matchups.indexWhere((m) => m.teamA == side || m.teamB == side);
          if (foundIndex != -1) {
            connections.add(_BracketConnection(back, slots[back][foundIndex], r, roundSlots[i]!));
            break;
          }
        }
      }
    }

    slots.add(roundSlots.cast<double>());
  }

  return _BracketSlotLayout(slots, connections);
}

/// Merges two conferences' own independently-computed bracket layouts
/// into one combined layout for the converging tree (see _BracketSection's
/// own docstring for why this can't just run _computeBracketSlotLayout on
/// a concatenated round list -- one conference's dedup fallback can steal
/// a slot from the other's). Conference B is stacked as a fixed band
/// directly underneath conference A's own tallest slot, so nothing in
/// either conference's dedup pass ever sees the other's rows at all.
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
  final offset = maxSlotA + 1;

  final slots = <List<double>>[];
  final connections = <_BracketConnection>[
    ...layoutA.connections,
    for (final c in layoutB.connections) _BracketConnection(c.fromRound, c.fromSlot + offset, c.toRound, c.toSlot + offset),
  ];
  for (var r = 0; r < roundCount; r++) {
    slots.add([...layoutA.slots[r], for (final slot in layoutB.slots[r]) slot + offset]);
  }

  // The championship's two sides are each conference's own last-round
  // winner -- trace which of that round's matchups produced it (same
  // winner-matching _computeBracketSlotLayout itself uses) so the
  // championship card converges at the right height instead of an
  // arbitrary fallback; a conference whose winner can't be traced (should
  // never happen in practice) just contributes no connector.
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

/// Draws each round-to-round connector as a simple 3-segment elbow
/// (horizontal out of the source card, vertical to the target's own row,
/// horizontal into the target card) -- the standard bracket-diagram
/// connector shape, positioned entirely from _computeBracketSlotLayout's
/// own slot math, no widget measurement needed.
///
/// A skip connection (see _BracketConnection.isSkip) is drawn dashed, in
/// `skipColor` instead of `color` -- one source card genuinely feeding
/// two different destinations (its own next round AND, for its other
/// side, a round further ahead) reads as a mistake if both lines look
/// identical; the visual break makes it legible as "this one jumps a
/// round" on sight instead.
class _BracketConnectorPainter extends CustomPainter {
  const _BracketConnectorPainter({
    required this.connections,
    required this.color,
    required this.skipColor,
    required this.cardWidth,
    required this.cardHeight,
    required this.roundGap,
    required this.verticalUnit,
  });

  final List<_BracketConnection> connections;
  final Color color;
  final Color skipColor;
  final double cardWidth;
  final double cardHeight;
  final double roundGap;
  final double verticalUnit;

  double _x(int round) => round * (cardWidth + roundGap);
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
      final midX = x1 + roundGap / 2;
      final paint = connection.isSkip ? skipPaint : normalPaint;
      _drawSegment(canvas, Offset(x1, y1), Offset(midX, y1), paint, dashed: connection.isSkip);
      _drawSegment(canvas, Offset(midX, y1), Offset(midX, y2), paint, dashed: connection.isSkip);
      _drawSegment(canvas, Offset(midX, y2), Offset(x2, y2), paint, dashed: connection.isSkip);
    }
  }

  @override
  bool shouldRepaint(covariant _BracketConnectorPainter oldDelegate) => oldDelegate.connections != connections;
}

/// Converging bracket tree -- rounds placed left to right, each matchup
/// card positioned by its own computed slot (see _computeBracketSlotLayout),
/// connector lines drawn underneath. Horizontally scrollable (the same
/// pattern this page's other wide content uses); the outer page itself
/// already scrolls vertically, so a fixed-height inner Stack here (driven
/// by the widest round's own card count) is safe -- Stack doesn't throw on
/// overflow the way a Row/Column would, so an unusually tall bracket just
/// extends the page's own scroll, never a RenderFlex error.
class _BracketTree extends StatelessWidget {
  const _BracketTree({
    required this.sport,
    required this.rounds,
    required this.teamNames,
    this.conferenceLabels = const [],
    this.precomputedLayout,
  });

  final String sport;
  final List<BracketRound> rounds;
  final Map<String, BracketTeamName> teamNames;

  /// Optional per-conference labels for a combined conference-split tree
  /// (see _BracketSection's own docstring) -- each names the round-0 slot
  /// where that conference's own band of matchups starts, so a small
  /// label can mark it since there's no longer a separate Column/heading
  /// per conference once both are merged into one Stack.
  final List<({String name, double slot})> conferenceLabels;

  /// A combined conference-split tree can't let _computeBracketSlotLayout
  /// run on the merged round list directly -- see
  /// _computeConferenceBracketLayout's own docstring for why -- so
  /// _BracketSection passes its own already-merged layout here instead.
  /// Null for the flat (NCAAFB) and independent-tree fallback cases,
  /// which still compute their own from `rounds` below.
  final _BracketSlotLayout? precomputedLayout;

  static const double _cardWidth = 220;
  static const double _cardHeight = 108;
  static const double _roundGap = 40;
  static const double _verticalUnit = 124;
  static const double _headerHeight = 20;

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
    final totalWidth = rounds.length * _cardWidth + (rounds.length - 1) * _roundGap;
    final totalHeight = maxSlot * _verticalUnit + _cardHeight;
    // Only NBA's own Play-In has a skip connection at all (see
    // _BracketConnection.isSkip's own doc comment) -- every other sport's
    // bracket never produces one, so the legend only earns its place here
    // when there's actually a dashed line on screen to explain.
    final hasSkipConnection = layout.connections.any((c) => c.isSkip);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (hasSkipConnection) ...[
          // No mainAxisSize.min here -- the explanatory text is long
          // enough to overflow a narrow viewport (a real RenderFlex
          // overflow, same class of bug this page has hit before) unless
          // it's allowed to wrap within the Row's own available width.
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
        SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          child: SizedBox(
            width: totalWidth,
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
            // Extra headroom (vs. the flat-bracket case's plain 10px gap)
            // when conference labels are present -- the first conference's
            // label sits at a negative `top` inside the Stack below (see
            // its own clipBehavior comment), and this reserves the actual
            // layout space for that overflow instead of just permitting
            // it to render into nothing, which is exactly the class of
            // RenderFlex-overflow bug this page has hit for real before.
            SizedBox(height: conferenceLabels.isEmpty ? 10 : 10 + _headerHeight),
            SizedBox(
              width: totalWidth,
              height: totalHeight,
              child: Stack(
                // conferenceLabels can sit slightly above the first
                // card's own top (a negative `top`, for the first
                // conference's label) -- Stack clips to its own bounds by
                // default, which would silently drop that label.
                clipBehavior: Clip.none,
                children: [
                  for (final label in conferenceLabels)
                    Positioned(
                      left: 0,
                      top: label.slot * _verticalUnit - _headerHeight,
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
                        // Muted but clearly visible against the dark
                        // background -- borderRaised (8% white) used to
                        // sit here, which was reported as barely legible.
                        color: AppColors.inkSub,
                        // A distinct accent (not inkSub's gray, not a
                        // signal color like warn/neg -- this isn't an
                        // error) plus a dashed stroke (see the painter's
                        // own docstring) for a skip connection.
                        skipColor: AppColors.violet,
                        cardWidth: _cardWidth,
                        cardHeight: _cardHeight,
                        roundGap: _roundGap,
                        verticalUnit: _verticalUnit,
                      ),
                    ),
                  ),
                  for (var r = 0; r < rounds.length; r++)
                    for (var i = 0; i < rounds[r].matchups.length; i++)
                      Positioned(
                        left: r * (_cardWidth + _roundGap),
                        top: layout.slots[r][i] * _verticalUnit,
                        width: _cardWidth,
                        height: _cardHeight,
                        child: _BracketMatchupCard(sport: sport, matchup: rounds[r].matchups[i], teamNames: teamNames),
                      ),
                ],
              ),
            ),
          ],
        ),
      ),
        ),
      ],
    );
  }
}

/// Small dashed-line swatch for the skip-connection legend (see
/// _BracketTree's own build()) -- reuses the connector painter's dash
/// geometry so the sample actually matches what's drawn on the real
/// connector lines, not just an approximation.
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
            // A series' running win count takes priority over a single
            // game's score -- shown throughout (0-0 included), not just
            // once final, since the record matters while it's still live.
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
  // BracketMatchup's own doc comment. A series matchup (isSeries) folds
  // in its running win-loss record -- "win probability" alone doesn't
  // convey a best-of-7 slot's real state the way it does for a true
  // single-elimination one.
  String _statusLabel() {
    final winnerLabel = matchup.predictedWinner != null ? _teamLabel(matchup.predictedWinner!) : null;
    if (matchup.isSeries) {
      final record = '${matchup.winsA}-${matchup.winsB}';
      // The predicted FINAL record, winner-first (e.g. "4-2"), distinct
      // from `record` above (the current/live one, 0-0 before the series
      // starts) -- see BracketMatchup.predictedWinsA/B's own doc comment.
      // Null once "final" (nothing left to predict) or if either side is
      // somehow missing, matching predictedWinner's own null-safety.
      final predictedRecord = matchup.predictedWinsA != null && matchup.predictedWinsB != null
          ? (matchup.predictedWinner == matchup.teamA
              ? '${matchup.predictedWinsA}-${matchup.predictedWinsB}'
              : '${matchup.predictedWinsB}-${matchup.predictedWinsA}')
          : null;
      switch (matchup.status) {
        case 'final':
          final winnerName = matchup.actualWinner != null ? _teamLabel(matchup.actualWinner!) : null;
          return winnerName != null ? '$winnerName WINS SERIES $record' : 'SERIES FINAL $record';
        case 'scheduled':
          if (matchup.winProbability == null || winnerLabel == null) return '$record — PREDICTION PENDING';
          final probability = '${(matchup.winProbability! * 100).round()}%';
          return predictedRecord != null
              ? '$record — $probability $winnerLabel (predicted $predictedRecord)'
              : '$record — $probability $winnerLabel';
        default:
          // A projected series slot still has a real (usually 0-0)
          // record -- the team rows above already show it per-team (see
          // this card's build()), but the status line itself was
          // dropping it, the only one of the 3 states to do so.
          if (matchup.winProbability == null || winnerLabel == null) return '$record — PROJECTED';
          final probability = '${(matchup.winProbability! * 100).round()}%';
          return predictedRecord != null
              ? '$record — PROJECTED — $probability $winnerLabel (predicted $predictedRecord)'
              : '$record — PROJECTED — $probability $winnerLabel';
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
  final String teamId;
  final int? seed;
  final Map<String, BracketTeamName> teamNames;
  final bool isWinner;
  final int? score;

  @override
  Widget build(BuildContext context) {
    final info = teamDisplayFor(sport, teamId, teamNames[teamId]?.abbreviation, apiColor: teamNames[teamId]?.color);
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

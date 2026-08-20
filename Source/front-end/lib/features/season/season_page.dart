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
            if (season.leaderboards != null || season.bracket != null || season.cupBracket != null) ...[
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
  return [
    // NCAAFB only.
    if (isNcaafb)
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
      return _BracketTree(sport: sport, rounds: flatRounds, teamNames: bracket.teamNames);
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
    );
  }
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
      final immediateSources = <double>[];
      for (var j = 0; j < previousMatchups.length; j++) {
        final previous = previousMatchups[j];
        if (previous.teamA == matchup.teamA || previous.teamA == matchup.teamB ||
            previous.teamB == matchup.teamA || previous.teamB == matchup.teamB) {
          immediateSources.add(previousSlots[j]);
        }
      }
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
  });

  final String sport;
  final List<BracketRound> rounds;
  final Map<String, BracketTeamName> teamNames;

  /// Per-conference labels for a combined conference-split tree; each names
  /// the round-0 slot where that conference's band of matchups starts.
  final List<({String name, double slot})> conferenceLabels;

  /// Precomputed layout for a combined conference-split tree (see
  /// _computeConferenceBracketLayout). Null for the flat (NCAAFB) and
  /// independent-tree fallback cases, which compute their own from
  /// `rounds` below.
  final _BracketSlotLayout? precomputedLayout;

  static const double _cardWidth = 220;
  static const double _cardHeight = 140;
  static const double _roundGap = 40;
  static const double _verticalUnit = 156;
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
                        color: AppColors.inkSub,
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
        ),
      ],
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
          return predictedRecord != null
              ? '$probability $winnerLabel (predicted $predictedRecord)'
              : '$probability $winnerLabel';
        default:
          if (matchup.winProbability == null || winnerLabel == null) return 'PROJECTED';
          final probability = '${(matchup.winProbability! * 100).round()}%';
          return predictedRecord != null
              ? 'PROJECTED — $probability $winnerLabel (predicted $predictedRecord)'
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

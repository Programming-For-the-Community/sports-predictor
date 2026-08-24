import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/data/season_repository.dart';
import 'package:front_end/core/models/season_projection.dart';
import 'package:front_end/features/season/season_page.dart';

import '../../support/mobile_viewport.dart';

TeamStanding _standing(String teamId, String division, {int wins = 6, int losses = 7, int ties = 1}) => TeamStanding(
      teamId: teamId,
      division: division,
      wins: wins,
      losses: losses,
      ties: ties,
      projectedWins: 9.4,
      projectedLosses: 7.6,
      divisionWinnerProbability: 0.42,
      playoffProbability: 0.61,
      championshipProbability: 0.08,
    );

/// Realistic-length names/divisions -- long enough to actually stress the
/// standings table's flexed columns and the leaderboard cards' name +
/// current->projected row.
final _season = SeasonProjection(
  sport: 'nfl',
  season: 2026,
  standings: [
    _standing('12', 'AFC West'),
    _standing('13', 'AFC West'),
    _standing('6', 'AFC South'),
    _standing('34', 'NFC West'),
  ],
  leaderboards: {
    'passing_yards': [
      const LeaderboardEntry(entityId: '1', name: 'Patrick Mahomes', currentTotal: 2100, projectedTotal: 4300),
      const LeaderboardEntry(entityId: '2', name: 'Christian McCaffrey-Johnson', currentTotal: 1900, projectedTotal: 4100),
    ],
    'receiving_touchdowns': [
      const LeaderboardEntry(entityId: '3', name: 'Amon-Ra St. Brown', currentTotal: 8, projectedTotal: 14),
    ],
  },
);

// Full 2-conference Cup knockout bracket (Semifinals -> Conference Final
// -> Championship), same shared _BracketSection/_BracketTree code path
// the main Playoff Bracket test below exercises.
final _nbaCupBracketSeason = SeasonProjection(
  sport: 'nba',
  season: 2026,
  standings: [
    const TeamStanding(
      teamId: '2', division: 'Eastern Atlantic', wins: 18, losses: 6, ties: 0,
      projectedWins: 58.0, projectedLosses: 24.0,
      divisionWinnerProbability: 0.7, playoffProbability: 0.95, championshipProbability: 0.18,
      playInProbability: 0.02,
    ),
  ],
  leaderboards: null,
  cupBracket: const BracketProjection(
    conferences: {
      'Eastern': [
        BracketRound(round: 'Semifinals', matchups: [
          BracketMatchup(teamA: '2', teamB: '8', seedA: 1, seedB: 4, status: 'projected', predictedWinner: '2', winProbability: 0.6),
          BracketMatchup(teamA: '19', teamB: '17', seedA: 2, seedB: 3, status: 'projected', predictedWinner: '19', winProbability: 0.55),
        ]),
        BracketRound(round: 'Conference Final', matchups: [
          BracketMatchup(teamA: '2', teamB: '19', seedA: 1, seedB: 2, status: 'projected', predictedWinner: '2', winProbability: 0.58),
        ]),
      ],
      'Western': [
        BracketRound(round: 'Semifinals', matchups: [
          BracketMatchup(teamA: '24', teamB: '25', seedA: 1, seedB: 4, status: 'projected', predictedWinner: '24', winProbability: 0.6),
          BracketMatchup(teamA: '7', teamB: '13', seedA: 2, seedB: 3, status: 'projected', predictedWinner: '7', winProbability: 0.52),
        ]),
        BracketRound(round: 'Conference Final', matchups: [
          BracketMatchup(teamA: '24', teamB: '7', seedA: 1, seedB: 2, status: 'projected', predictedWinner: '24', winProbability: 0.57),
        ]),
      ],
    },
    rounds: null,
    teamNames: {},
    finalMatchup: BracketMatchup(teamA: '2', teamB: '24', status: 'projected', predictedWinner: '24', winProbability: 0.51),
    champion: '24',
  ),
);

// Full 3-round conference-split bracket plus a championship card -- the
// widest content this page has (multiple horizontally-scrolling round
// columns stacked per conference).
final _bracketSeason = SeasonProjection(
  sport: 'nfl',
  season: 2026,
  standings: [_standing('12', 'AFC West')],
  leaderboards: null,
  bracket: const BracketProjection(
    conferences: {
      'AFC': [
        BracketRound(round: 'Wild Card', matchups: [
          BracketMatchup(teamA: '12', teamB: '13', seedA: 2, seedB: 7, status: 'projected', predictedWinner: '12', winProbability: 0.62),
          BracketMatchup(teamA: '2', teamB: '17', seedA: 3, seedB: 6, status: 'scheduled', predictedWinner: '2', winProbability: 0.58),
          BracketMatchup(
            teamA: '4', teamB: '5', seedA: 4, seedB: 5, status: 'final',
            predictedWinner: '4', winProbability: 0.51, actualWinner: '5', actualHomeScore: 20, actualAwayScore: 24,
          ),
        ]),
        BracketRound(round: 'Divisional', matchups: [
          BracketMatchup(teamA: '1', teamB: '12', seedA: 1, seedB: 2, status: 'projected', predictedWinner: '1', winProbability: 0.55),
          BracketMatchup(teamA: '2', teamB: '5', seedA: 3, seedB: 5, status: 'projected', predictedWinner: '2', winProbability: 0.6),
        ]),
        BracketRound(round: 'Conference Championship', matchups: [
          BracketMatchup(teamA: '1', teamB: '2', seedA: 1, seedB: 3, status: 'projected', predictedWinner: '1', winProbability: 0.53),
        ]),
      ],
      'NFC': [
        BracketRound(round: 'Wild Card', matchups: [
          BracketMatchup(teamA: '19', teamB: '24', seedA: 2, seedB: 7, status: 'projected', predictedWinner: '19', winProbability: 0.55),
          BracketMatchup(teamA: '21', teamB: '25', seedA: 3, seedB: 6, status: 'projected', predictedWinner: '21', winProbability: 0.52),
          BracketMatchup(teamA: '18', teamB: '20', seedA: 4, seedB: 5, status: 'projected', predictedWinner: '18', winProbability: 0.5),
        ]),
        BracketRound(round: 'Divisional', matchups: [
          BracketMatchup(teamA: '22', teamB: '19', seedA: 1, seedB: 2, status: 'projected', predictedWinner: '22', winProbability: 0.51),
          BracketMatchup(teamA: '21', teamB: '18', seedA: 3, seedB: 4, status: 'projected', predictedWinner: '21', winProbability: 0.5),
        ]),
        BracketRound(round: 'Conference Championship', matchups: [
          BracketMatchup(teamA: '22', teamB: '21', seedA: 1, seedB: 3, status: 'projected', predictedWinner: '22', winProbability: 0.52),
        ]),
      ],
    },
    rounds: null,
    teamNames: {
      '1': BracketTeamName(name: 'Baltimore Ravens', abbreviation: 'BAL'),
      '22': BracketTeamName(name: 'Arizona Cardinals', abbreviation: 'ARI'),
    },
    finalMatchup: BracketMatchup(teamA: '1', teamB: '22', status: 'projected', predictedWinner: '1', winProbability: 0.51),
    champion: '1',
  ),
);

// Exercises a best-of-7 series matchup card. Every matchup below is
// deliberately near the longest realistic status string (a 3-digit
// probability, a 4-3 predicted record, a real team abbreviation) so a
// too-small _cardHeight/_verticalUnit shows up here, not just in a single
// hand-picked test case.
final _nbaSeriesBracketSeason = SeasonProjection(
  sport: 'nba',
  season: 2026,
  standings: [],
  leaderboards: null,
  bracket: const BracketProjection(
    conferences: {
      'Eastern': [
        BracketRound(round: 'Play-In', matchups: [
          BracketMatchup(teamA: '1', teamB: '14', seedA: 7, seedB: 8, status: 'projected', predictedWinner: '1', winProbability: 0.59),
        ]),
        BracketRound(round: 'Conference Quarterfinals', matchups: [
          BracketMatchup(
            teamA: '18', teamB: '28', seedA: 1, seedB: 8, status: 'projected',
            predictedWinner: '18', winProbability: 0.995, winsA: 0, winsB: 0,
            predictedWinsA: 4, predictedWinsB: 3,
          ),
          BracketMatchup(
            teamA: '2', teamB: '5', seedA: 4, seedB: 5, status: 'scheduled',
            predictedWinner: '2', winProbability: 0.813, winsA: 3, winsB: 3,
            predictedWinsA: 4, predictedWinsB: 3,
          ),
        ]),
        BracketRound(round: 'Conference Semifinals', matchups: [
          BracketMatchup(
            teamA: '18', teamB: '2', seedA: 1, seedB: 4, status: 'final',
            winsA: 4, winsB: 3, actualWinner: '18',
          ),
        ]),
      ],
      'Western': [
        BracketRound(round: 'Play-In', matchups: [
          BracketMatchup(teamA: '21', teamB: '3', seedA: 7, seedB: 8, status: 'projected', predictedWinner: '21', winProbability: 0.51),
        ]),
        BracketRound(round: 'Conference Quarterfinals', matchups: [
          BracketMatchup(
            teamA: '24', teamB: '12', seedA: 1, seedB: 8, status: 'projected',
            predictedWinner: '24', winProbability: 0.812, winsA: 0, winsB: 0,
            predictedWinsA: 4, predictedWinsB: 1,
          ),
        ]),
      ],
    },
    rounds: null,
    teamNames: {
      '18': BracketTeamName(name: 'Boston Celtics', abbreviation: 'BOS'),
      '28': BracketTeamName(name: 'Toronto Raptors', abbreviation: 'TOR'),
    },
    finalMatchup: BracketMatchup(teamA: '18', teamB: '24', status: 'projected', predictedWinner: '18', winProbability: 0.5),
    champion: '18',
  ),
);

// Full 4-region March Madness bracket, First Four through Championship --
// the region-stacked _MarchMadnessSection layout, distinct from the
// 2-conference _BracketSection path every other test fixture above
// exercises. Realistic-length team names, same stress-test convention as
// _nbaSeriesBracketSeason above.
RegionBracket _region(String championA, String championB, String champion) => RegionBracket(
      rounds: [
        BracketRound(round: 'Round of 64', matchups: [
          BracketMatchup(teamA: championA, teamB: '${championA}b', seedA: 1, seedB: 16, status: 'projected', predictedWinner: championA, winProbability: 0.9),
          BracketMatchup(teamA: championB, teamB: '${championB}b', seedA: 8, seedB: 9, status: 'projected', predictedWinner: championB, winProbability: 0.55),
        ]),
        BracketRound(round: 'Round of 32', matchups: [
          BracketMatchup(teamA: championA, teamB: championB, seedA: 1, seedB: 8, status: 'projected', predictedWinner: champion, winProbability: 0.65),
        ]),
      ],
      champion: champion,
    );

final _marchMadnessSeason = SeasonProjection(
  sport: 'ncaambb',
  season: 2027,
  standings: [],
  leaderboards: null,
  marchMadnessBracket: MarchMadnessBracket(
    firstFour: const [
      BracketMatchup(teamA: '90', teamB: '91', status: 'projected', predictedWinner: '90', winProbability: 0.55),
      BracketMatchup(teamA: '92', teamB: '93', status: 'projected', predictedWinner: '92', winProbability: 0.51),
    ],
    regions: {
      'Region A': _region('1', '9', '1'),
      'Region B': _region('2', '10', '2'),
      'Region C': _region('3', '11', '3'),
      'Region D': _region('4', '12', '4'),
    },
    finalFour: const [
      BracketMatchup(teamA: '1', teamB: '2', status: 'projected', predictedWinner: '1', winProbability: 0.52),
      BracketMatchup(teamA: '3', teamB: '4', status: 'projected', predictedWinner: '3', winProbability: 0.58),
    ],
    championship: const BracketMatchup(teamA: '1', teamB: '3', status: 'projected', predictedWinner: '1', winProbability: 0.51),
    champion: '1',
    teamNames: const {
      '1': BracketTeamName(name: 'Duke Blue Devils', abbreviation: 'DUKE'),
      '3': BracketTeamName(name: 'Gonzaga Bulldogs', abbreviation: 'GONZ'),
    },
  ),
);

void main() {
  for (final width in mobileViewportWidths) {
    testWidgets('standings tab renders with no overflow at ${width}px wide', (tester) async {
      await pumpAtWidth(
        tester,
        width,
        ProviderScope(
          overrides: [seasonProjectionProvider.overrideWith((ref, sport) async => _season)],
          child: const MaterialApp(home: Scaffold(body: SeasonPage(sportId: 'nfl'))),
        ),
      );

      expect(tester.takeException(), isNull);
    });

    testWidgets('player prop leaders tab renders with no overflow at ${width}px wide', (tester) async {
      await pumpAtWidth(
        tester,
        width,
        ProviderScope(
          overrides: [seasonProjectionProvider.overrideWith((ref, sport) async => _season)],
          child: const MaterialApp(home: Scaffold(body: SeasonPage(sportId: 'nfl'))),
        ),
      );

      // The toggle row scrolls horizontally on a narrow viewport --
      // ensureVisible scrolls it into reach first.
      await tester.ensureVisible(find.text('Player Prop Leaders'));
      await tester.tap(find.text('Player Prop Leaders'));
      await tester.pumpAndSettle();

      expect(tester.takeException(), isNull);
    });

    testWidgets('NBA Cup Bracket tab renders a full 2-conference bracket with no overflow at ${width}px wide', (tester) async {
      await pumpAtWidth(
        tester,
        width,
        ProviderScope(
          overrides: [seasonProjectionProvider.overrideWith((ref, sport) async => _nbaCupBracketSeason)],
          child: const MaterialApp(home: Scaffold(body: SeasonPage(sportId: 'nba'))),
        ),
      );

      await tester.ensureVisible(find.text('NBA Cup Bracket'));
      await tester.tap(find.text('NBA Cup Bracket'));
      await tester.pumpAndSettle();

      expect(tester.takeException(), isNull);
    });

    testWidgets('Playoff Bracket tab renders a full 2-conference, 3-round bracket with no overflow at ${width}px wide', (tester) async {
      await pumpAtWidth(
        tester,
        width,
        ProviderScope(
          overrides: [seasonProjectionProvider.overrideWith((ref, sport) async => _bracketSeason)],
          child: const MaterialApp(home: Scaffold(body: SeasonPage(sportId: 'nfl'))),
        ),
      );

      await tester.ensureVisible(find.text('Playoff Bracket'));
      await tester.tap(find.text('Playoff Bracket'));
      await tester.pumpAndSettle();

      expect(tester.takeException(), isNull);
    });

    testWidgets('Playoff Bracket tab renders NBA series matchups (long status lines) with no overflow at ${width}px wide',
        (tester) async {
      await pumpAtWidth(
        tester,
        width,
        ProviderScope(
          overrides: [seasonProjectionProvider.overrideWith((ref, sport) async => _nbaSeriesBracketSeason)],
          child: const MaterialApp(home: Scaffold(body: SeasonPage(sportId: 'nba'))),
        ),
      );

      await tester.ensureVisible(find.text('Playoff Bracket'));
      await tester.tap(find.text('Playoff Bracket'));
      await tester.pumpAndSettle();

      expect(tester.takeException(), isNull);
    });

    testWidgets('March Madness tab renders a full 4-region bracket (First Four through Championship) with no overflow at ${width}px wide',
        (tester) async {
      await pumpAtWidth(
        tester,
        width,
        ProviderScope(
          overrides: [seasonProjectionProvider.overrideWith((ref, sport) async => _marchMadnessSeason)],
          child: const MaterialApp(home: Scaffold(body: SeasonPage(sportId: 'ncaambb'))),
        ),
      );

      await tester.ensureVisible(find.text('March Madness'));
      await tester.tap(find.text('March Madness'));
      await tester.pumpAndSettle();

      expect(tester.takeException(), isNull);
    });
  }
}

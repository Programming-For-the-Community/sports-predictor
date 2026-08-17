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
/// current->projected row, not just short placeholder strings that would
/// pass trivially regardless of whether the layout is actually safe.
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

// Realistic group size (5 teams, real-length abbreviations aren't long
// enough to stress anything on their own, but a full group plus every
// probability column together is a genuinely wide row) -- same "don't
// just use short placeholder strings" philosophy as _season above.
final _nbaCupSeason = SeasonProjection(
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
  cup: const CupProjection(groups: {
    'Eastern B': [
      CupTeamStanding(
        teamId: '2', name: 'Boston Celtics', abbreviation: 'BOS', groupWins: 4, groupLosses: 1,
        groupWinnerProbability: 0.55, knockoutProbability: 0.7, cupFinalistProbability: 0.32, championProbability: 0.14,
      ),
      CupTeamStanding(
        teamId: '19', name: 'Orlando Magic', abbreviation: 'ORL', groupWins: 3, groupLosses: 2,
        groupWinnerProbability: 0.2, knockoutProbability: 0.35, cupFinalistProbability: 0.11, championProbability: 0.03,
      ),
      CupTeamStanding(
        teamId: '8', name: 'Detroit Pistons', abbreviation: 'DET', groupWins: 2, groupLosses: 3,
        groupWinnerProbability: 0.15, knockoutProbability: 0.22, cupFinalistProbability: 0.05, championProbability: 0.01,
      ),
      CupTeamStanding(
        teamId: '20', name: 'Philadelphia 76ers', abbreviation: 'PHI', groupWins: 2, groupLosses: 3,
        groupWinnerProbability: 0.08, knockoutProbability: 0.15, cupFinalistProbability: 0.03, championProbability: 0.01,
      ),
      CupTeamStanding(
        teamId: '17', name: 'Brooklyn Nets', abbreviation: 'BKN', groupWins: 1, groupLosses: 4,
        groupWinnerProbability: 0.02, knockoutProbability: 0.04, cupFinalistProbability: 0.01, championProbability: 0.0,
      ),
    ],
  }),
);

// Full 3-round conference-split bracket plus a championship card -- the
// widest content this page has (multiple horizontally-scrolling round
// columns stacked per conference), same "realistic, not a trivial
// placeholder" philosophy as _season/_nbaCupSeason above.
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

      // The toggle row scrolls horizontally on a narrow viewport (see
      // season_page.dart) -- ensureVisible scrolls it into reach first,
      // same as a real user would have to.
      await tester.ensureVisible(find.text('Player Prop Leaders'));
      await tester.tap(find.text('Player Prop Leaders'));
      await tester.pumpAndSettle();

      expect(tester.takeException(), isNull);
    });

    testWidgets('NBA Cup tab renders a full 5-team group with no overflow at ${width}px wide', (tester) async {
      await pumpAtWidth(
        tester,
        width,
        ProviderScope(
          overrides: [seasonProjectionProvider.overrideWith((ref, sport) async => _nbaCupSeason)],
          child: const MaterialApp(home: Scaffold(body: SeasonPage(sportId: 'nba'))),
        ),
      );

      await tester.ensureVisible(find.text('NBA Cup'));
      await tester.tap(find.text('NBA Cup'));
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
  }
}

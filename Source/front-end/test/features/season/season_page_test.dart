import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/data/season_repository.dart';
import 'package:front_end/core/models/season_projection.dart';
import 'package:front_end/features/season/season_page.dart';

/// Functional (not just overflow) coverage for the season page's 3-way
/// tab logic (standings/player-prop-leaders/NBA-Cup) and NBA's own
/// standings column set -- season_page_mobile_test.dart only asserts "no
/// overflow", not which content actually renders.
TeamStanding _nbaStanding(String teamId, {double playInProbability = 0.1}) => TeamStanding(
      teamId: teamId,
      division: 'Eastern Atlantic',
      wins: 10,
      losses: 5,
      ties: 0,
      projectedWins: 55.0,
      projectedLosses: 27.0,
      divisionWinnerProbability: 0.2,
      playoffProbability: 0.7,
      championshipProbability: 0.05,
      playInProbability: playInProbability,
    );

void main() {
  Future<void> pumpSeasonPage(WidgetTester tester, String sportId, SeasonProjection projection) async {
    await tester.pumpWidget(
      ProviderScope(
        overrides: [seasonProjectionProvider.overrideWith((ref, sport) async => projection)],
        child: MaterialApp(home: Scaffold(body: SeasonPage(sportId: sportId))),
      ),
    );
    await tester.pumpAndSettle();
  }

  testWidgets('nba standings show PLAY-IN% instead of DIV%', (tester) async {
    final projection = SeasonProjection(
      sport: 'nba', season: 2026, standings: [_nbaStanding('2')], leaderboards: null,
    );

    await pumpSeasonPage(tester, 'nba', projection);

    expect(find.text('PLAY-IN%'), findsOneWidget);
    expect(find.text('DIV%'), findsNothing);
    expect(find.text('PLAYOFFS%'), findsOneWidget);
    expect(find.text('CHAMP%'), findsOneWidget);
  });

  testWidgets('nfl standings keep DIV%/PO%/SB%, no PLAY-IN%', (tester) async {
    final projection = SeasonProjection(
      sport: 'nfl',
      season: 2025,
      standings: [
        TeamStanding(
          teamId: '12', division: 'AFC West', wins: 10, losses: 5, ties: 0,
          projectedWins: 12.0, projectedLosses: 5.0,
          divisionWinnerProbability: 0.6, playoffProbability: 0.8, championshipProbability: 0.1,
        ),
      ],
      leaderboards: null,
    );

    await pumpSeasonPage(tester, 'nfl', projection);

    expect(find.text('DIV%'), findsOneWidget);
    expect(find.text('PLAY-IN%'), findsNothing);
  });

  testWidgets('no toggle row at all when neither leaderboards nor cup is present', (tester) async {
    final projection = SeasonProjection(sport: 'nba', season: 2026, standings: [_nbaStanding('2')], leaderboards: null);

    await pumpSeasonPage(tester, 'nba', projection);

    expect(find.text('Standings & Playoff Odds'), findsNothing);
    expect(find.text('NBA Cup'), findsNothing);
  });

  testWidgets('NBA Cup toggle appears when cup is present and switches to the group view', (tester) async {
    final projection = SeasonProjection(
      sport: 'nba',
      season: 2026,
      standings: [_nbaStanding('2')],
      leaderboards: null,
      cup: const CupProjection(groups: {
        'Eastern B': [
          CupTeamStanding(
            teamId: '2', abbreviation: 'BOS', groupWins: 4, groupLosses: 1,
            groupWinnerProbability: 0.6, knockoutProbability: 0.7, cupFinalistProbability: 0.3, championProbability: 0.1,
          ),
        ],
      }),
    );

    await pumpSeasonPage(tester, 'nba', projection);

    expect(find.text('NBA Cup'), findsOneWidget);
    // Standings tab is the default -- the Cup group isn't visible yet.
    expect(find.text('EASTERN B'), findsNothing);

    await tester.tap(find.text('NBA Cup'));
    await tester.pumpAndSettle();

    expect(find.text('EASTERN B'), findsOneWidget);
    expect(find.text('BOS'), findsOneWidget);
    expect(find.text('4-1'), findsOneWidget);
  });

  testWidgets('Playoff Bracket toggle appears when bracket is present and switches to the bracket view', (tester) async {
    final projection = SeasonProjection(
      sport: 'nfl',
      season: 2026,
      standings: [_nbaStanding('2')], // reused fixture, sport-neutral shape
      leaderboards: null,
      bracket: const BracketProjection(
        conferences: {
          'AFC': [
            BracketRound(round: 'Wild Card', matchups: [
              BracketMatchup(
                teamA: '12', teamB: '13', seedA: 2, seedB: 7, status: 'projected',
                predictedWinner: '12', winProbability: 0.62,
              ),
            ]),
          ],
          'NFC': [
            BracketRound(round: 'Wild Card', matchups: [
              BracketMatchup(
                teamA: '19', teamB: '24', seedA: 2, seedB: 7, status: 'projected',
                predictedWinner: '19', winProbability: 0.55,
              ),
            ]),
          ],
        },
        rounds: null,
        teamNames: {},
        finalMatchup: BracketMatchup(teamA: '12', teamB: '19', status: 'projected', predictedWinner: '12', winProbability: 0.51),
        champion: '12',
      ),
    );

    await pumpSeasonPage(tester, 'nfl', projection);

    expect(find.text('Playoff Bracket'), findsOneWidget);
    // Standings tab is the default -- bracket content isn't visible yet.
    expect(find.text('AFC'), findsNothing);

    await tester.tap(find.text('Playoff Bracket'));
    await tester.pumpAndSettle();

    expect(find.text('AFC'), findsOneWidget);
    expect(find.text('NFC'), findsOneWidget);
    expect(find.text('CHAMPIONSHIP'), findsOneWidget);
    expect(find.text('WILD CARD'), findsNWidgets(2));
  });

  testWidgets('a flat (NCAAFB-shaped) bracket renders its rounds with no conference headers', (tester) async {
    final projection = SeasonProjection(
      sport: 'ncaafb',
      season: 2025,
      standings: [],
      leaderboards: null,
      bracket: const BracketProjection(
        conferences: {},
        rounds: [
          BracketRound(round: 'Round of 12', matchups: [
            BracketMatchup(teamA: '5', teamB: '12', seedA: 5, seedB: 12, status: 'projected', predictedWinner: '5', winProbability: 0.7),
          ]),
          BracketRound(round: 'National Championship', matchups: [
            BracketMatchup(teamA: '1', teamB: '2', seedA: 1, seedB: 2, status: 'projected', predictedWinner: '1', winProbability: 0.58),
          ]),
        ],
        teamNames: {},
        champion: '1',
      ),
    );

    await pumpSeasonPage(tester, 'ncaafb', projection);
    await tester.tap(find.text('Playoff Bracket'));
    await tester.pumpAndSettle();

    expect(find.text('ROUND OF 12'), findsOneWidget);
    expect(find.text('NATIONAL CHAMPIONSHIP'), findsOneWidget);
    expect(find.text('CHAMPIONSHIP'), findsNothing); // that label is only the conference-split final's own header
  });

  testWidgets('a completed bracket matchup shows the real score, not a probability', (tester) async {
    final projection = SeasonProjection(
      sport: 'nfl',
      season: 2026,
      standings: [],
      leaderboards: null,
      bracket: const BracketProjection(
        conferences: {
          'AFC': [
            BracketRound(round: 'Wild Card', matchups: [
              BracketMatchup(
                teamA: '12', teamB: '13', seedA: 2, seedB: 7, status: 'final',
                predictedWinner: '12', winProbability: 0.62,
                actualWinner: '12', actualHomeScore: 27, actualAwayScore: 13,
              ),
            ]),
          ],
        },
        rounds: null,
        teamNames: {},
      ),
    );

    await pumpSeasonPage(tester, 'nfl', projection);
    await tester.tap(find.text('Playoff Bracket'));
    await tester.pumpAndSettle();

    expect(find.text('FINAL'), findsOneWidget);
    expect(find.text('27'), findsOneWidget);
    expect(find.text('13'), findsOneWidget);
  });

  testWidgets('ncaafb has neither PLAY-IN% nor an NBA Cup toggle', (tester) async {
    final projection = SeasonProjection(
      sport: 'ncaafb',
      season: 2025,
      standings: [
        TeamStanding(
          teamId: '99', division: 'SEC', wins: 10, losses: 2, ties: 0,
          projectedWins: 11.0, projectedLosses: 2.0,
          divisionWinnerProbability: 0.4, playoffProbability: 0.6, championshipProbability: 0.15,
        ),
      ],
      leaderboards: null,
    );

    await pumpSeasonPage(tester, 'ncaafb', projection);

    expect(find.text('PLAY-IN%'), findsNothing);
    expect(find.text('NBA Cup'), findsNothing);
    expect(find.text('CONF%'), findsOneWidget);
  });
}

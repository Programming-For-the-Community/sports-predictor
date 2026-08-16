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

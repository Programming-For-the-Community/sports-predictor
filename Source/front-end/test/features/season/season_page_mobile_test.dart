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
  }
}

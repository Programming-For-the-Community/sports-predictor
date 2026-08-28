import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/data/pga_season_repository.dart';
import 'package:front_end/core/models/pga_season_projection.dart';
import 'package:front_end/features/season/pga_season_page.dart';

import '../../support/mobile_viewport.dart';

/// Dedicated mobile check for PgaSeasonPage's own 7-column standings
/// table (#, GOLFER, POINTS, ST. JUDE%, BMW%, TOUR CH.%, CHAMP%) -- the
/// tightest real column count this project's own field-event tables use,
/// same overflow-risk shape field_leaderboard_table.dart's own mobile
/// tests already guard.
final _projection = PgaSeasonProjection(
  season: 2026,
  simulations: 750,
  standings: [
    const PgaFedexStanding(
      entityId: '1', name: 'Christopher Alexander Thornbury-Whitmore III', country: 'United States of America',
      currentPoints: 5234.7, projectedPoints: 6123.45,
      fedexStJudeProbability: 0.987, bmwProbability: 0.912, tourChampionshipProbability: 0.845, championProbability: 0.312,
    ),
    const PgaFedexStanding(
      entityId: '2', name: 'Rory McIlroy', country: 'Northern Ireland',
      currentPoints: 4800.0, projectedPoints: 5600.2,
      fedexStJudeProbability: 0.95, bmwProbability: 0.8, tourChampionshipProbability: 0.6, championProbability: 0.2,
    ),
  ],
);

void main() {
  for (final width in mobileViewportWidths) {
    testWidgets('renders the FedEx Cup standings table with no overflow at ${width}px wide', (tester) async {
      await pumpAtWidth(
        tester,
        width,
        ProviderScope(
          overrides: [pgaSeasonProjectionProvider.overrideWith((ref) async => _projection)],
          child: const MaterialApp(home: Scaffold(body: PgaSeasonPage())),
        ),
      );

      expect(tester.takeException(), isNull);

      // Below _compactBreakpoint, ST. JUDE%/BMW%/TOUR CH.% drop out of
      // the top-level columns entirely -- crushing all 7 into a ~340px
      // usable width is what made the table illegible on mobile.
      expect(find.text('ST. JUDE%'), findsNothing);
      expect(find.text('BMW%'), findsNothing);
      expect(find.text('TOUR CH.%'), findsNothing);
      // #, GOLFER, POINTS, CHAMP% stay visible.
      expect(find.text('CHAMP%'), findsOneWidget);
      expect(find.text('Rory McIlroy'), findsOneWidget);
    });

    testWidgets('tapping a row at ${width}px wide reveals the Playoffs-field odds', (tester) async {
      await pumpAtWidth(
        tester,
        width,
        ProviderScope(
          overrides: [pgaSeasonProjectionProvider.overrideWith((ref) async => _projection)],
          child: const MaterialApp(home: Scaffold(body: PgaSeasonPage())),
        ),
      );

      expect(find.text('ST. JUDE%'), findsNothing);

      await tester.tap(find.text('Rory McIlroy'));
      await tester.pumpAndSettle();

      expect(find.text('ST. JUDE%'), findsOneWidget);
      expect(find.text('95%'), findsOneWidget); // Rory's fedexStJudeProbability, 0.95 rounded
    });
  }
}

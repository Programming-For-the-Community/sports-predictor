import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/data/pga_season_repository.dart';
import 'package:front_end/core/models/pga_season_projection.dart';
import 'package:front_end/features/season/pga_season_page.dart';

PgaFedexStanding _standing(
  String entityId, {
  String? name,
  double currentPoints = 0,
  double projectedPoints = 0,
  double championProbability = 0,
}) =>
    PgaFedexStanding(
      entityId: entityId,
      name: name,
      country: 'USA',
      currentPoints: currentPoints,
      projectedPoints: projectedPoints,
      fedexStJudeProbability: 0.5,
      bmwProbability: 0.3,
      tourChampionshipProbability: 0.1,
      championProbability: championProbability,
    );

Widget _wrap(PgaSeasonProjection projection) => ProviderScope(
      overrides: [pgaSeasonProjectionProvider.overrideWith((ref) async => projection)],
      child: const MaterialApp(home: Scaffold(body: PgaSeasonPage())),
    );

void main() {
  testWidgets('shows the season year and every golfer in the standings', (tester) async {
    final projection = PgaSeasonProjection(
      season: 2026,
      simulations: 750,
      standings: [
        _standing('1', name: 'Scottie Scheffler', currentPoints: 5200, projectedPoints: 6100),
        _standing('2', name: 'Rory McIlroy', currentPoints: 4800, projectedPoints: 5600),
      ],
    );

    await tester.pumpWidget(_wrap(projection));
    await tester.pumpAndSettle();

    expect(find.textContaining('2026'), findsWidgets);
    expect(find.text('Scottie Scheffler'), findsOneWidget);
    expect(find.text('Rory McIlroy'), findsOneWidget);
  });

  testWidgets('shows simulated-count copy when simulations > 0', (tester) async {
    final projection = PgaSeasonProjection(season: 2026, simulations: 500, standings: [_standing('1', name: 'A')]);

    await tester.pumpWidget(_wrap(projection));
    await tester.pumpAndSettle();

    expect(find.textContaining('500 season simulations'), findsOneWidget);
  });

  testWidgets('shows real-final-standings copy when simulations is 0', (tester) async {
    final projection = PgaSeasonProjection(season: 2026, simulations: 0, standings: [_standing('1', name: 'A')]);

    await tester.pumpWidget(_wrap(projection));
    await tester.pumpAndSettle();

    expect(find.textContaining('Season complete'), findsOneWidget);
  });

  testWidgets('an empty standings list shows a plain message instead of a table', (tester) async {
    final projection = PgaSeasonProjection(season: 2026, simulations: 0, standings: []);

    await tester.pumpWidget(_wrap(projection));
    await tester.pumpAndSettle();

    expect(find.textContaining('No FedEx Cup standings'), findsOneWidget);
  });

  testWidgets('a load error shows a real message, not a blank page', (tester) async {
    await tester.pumpWidget(ProviderScope(
      overrides: [pgaSeasonProjectionProvider.overrideWith((ref) async => throw Exception('boom'))],
      child: const MaterialApp(home: Scaffold(body: PgaSeasonPage())),
    ));
    await tester.pumpAndSettle();

    expect(find.textContaining('Couldn\'t load the FedEx Cup standings'), findsOneWidget);
  });
}

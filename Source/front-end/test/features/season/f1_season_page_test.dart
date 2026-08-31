import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/data/f1_season_repository.dart';
import 'package:front_end/core/models/f1_season_projection.dart';
import 'package:front_end/features/season/f1_season_page.dart';

F1DriverStanding _driver(String entityId, {String? name, double currentPoints = 0, double projectedPoints = 0, double championProbability = 0}) =>
    F1DriverStanding(entityId: entityId, name: name, currentPoints: currentPoints, projectedPoints: projectedPoints, championProbability: championProbability);

F1ConstructorStanding _constructor(String entityId, {String? name, double currentPoints = 0, double projectedPoints = 0, double championProbability = 0}) =>
    F1ConstructorStanding(entityId: entityId, name: name, currentPoints: currentPoints, projectedPoints: projectedPoints, championProbability: championProbability);

Widget _wrap(F1SeasonProjection projection) => ProviderScope(
      overrides: [f1SeasonProjectionProvider.overrideWith((ref) async => projection)],
      child: const MaterialApp(home: Scaffold(body: F1SeasonPage())),
    );

void main() {
  testWidgets('defaults to the Drivers\' Championship tab', (tester) async {
    final projection = F1SeasonProjection(
      season: 2026,
      simulations: 750,
      driverStandings: [
        _driver('1', name: 'Max Verstappen', currentPoints: 350, projectedPoints: 410),
        _driver('2', name: 'Lando Norris', currentPoints: 320, projectedPoints: 380),
      ],
      constructorStandings: [
        _constructor('1', name: 'Red Bull', currentPoints: 600, projectedPoints: 700),
        _constructor('2', name: 'McLaren', currentPoints: 550, projectedPoints: 640),
      ],
    );

    await tester.pumpWidget(_wrap(projection));
    await tester.pumpAndSettle();

    expect(find.textContaining('2026'), findsWidgets);
    expect(find.text('Max Verstappen'), findsOneWidget);
    expect(find.text('Lando Norris'), findsOneWidget);
    // Constructors' own table isn't built at all until that tab is tapped.
    expect(find.text('Red Bull'), findsNothing);
    expect(find.text('McLaren'), findsNothing);
  });

  testWidgets('tapping the Constructors\' Championship tab swaps to that table', (tester) async {
    final projection = F1SeasonProjection(
      season: 2026,
      simulations: 750,
      driverStandings: [_driver('1', name: 'Max Verstappen')],
      constructorStandings: [_constructor('1', name: 'Red Bull'), _constructor('2', name: 'McLaren')],
    );

    await tester.pumpWidget(_wrap(projection));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Constructors\' Championship'));
    await tester.pumpAndSettle();

    expect(find.text('Red Bull'), findsOneWidget);
    expect(find.text('McLaren'), findsOneWidget);
    // Driver table is gone now that the other tab is active.
    expect(find.text('Max Verstappen'), findsNothing);
  });

  testWidgets('shows simulated-count copy when simulations > 0', (tester) async {
    final projection = F1SeasonProjection(season: 2026, simulations: 500, driverStandings: [_driver('1', name: 'A')], constructorStandings: []);

    await tester.pumpWidget(_wrap(projection));
    await tester.pumpAndSettle();

    expect(find.textContaining('500 season simulations'), findsOneWidget);
  });

  testWidgets('shows real-final-standings copy when simulations is 0', (tester) async {
    final projection = F1SeasonProjection(season: 2026, simulations: 0, driverStandings: [_driver('1', name: 'A')], constructorStandings: []);

    await tester.pumpWidget(_wrap(projection));
    await tester.pumpAndSettle();

    expect(find.textContaining('Season complete'), findsOneWidget);
  });

  testWidgets('an empty driver standings list shows a plain message instead of a table', (tester) async {
    final projection = F1SeasonProjection(season: 2026, simulations: 0, driverStandings: [], constructorStandings: []);

    await tester.pumpWidget(_wrap(projection));
    await tester.pumpAndSettle();

    expect(find.textContaining('No championship standings'), findsOneWidget);
  });

  testWidgets('no constructor standings yet -- tab still reachable, shows a plain message instead of a table', (tester) async {
    final projection = F1SeasonProjection(season: 2026, simulations: 0, driverStandings: [_driver('1', name: 'A')], constructorStandings: []);

    await tester.pumpWidget(_wrap(projection));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Constructors\' Championship'));
    await tester.pumpAndSettle();

    expect(find.textContaining('No constructor standings yet'), findsOneWidget);
  });

  testWidgets('a load error shows a real message, not a blank page', (tester) async {
    await tester.pumpWidget(ProviderScope(
      overrides: [f1SeasonProjectionProvider.overrideWith((ref) async => throw Exception('boom'))],
      child: const MaterialApp(home: Scaffold(body: F1SeasonPage())),
    ));
    await tester.pumpAndSettle();

    expect(find.textContaining('Couldn\'t load the championship standings'), findsOneWidget);
  });
}

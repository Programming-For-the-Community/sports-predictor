import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/data/f1_season_repository.dart';
import 'package:front_end/core/models/f1_season_projection.dart';
import 'package:front_end/features/season/f1_season_page.dart';

import '../../support/mobile_viewport.dart';

final _projection = F1SeasonProjection(
  season: 2026,
  simulations: 750,
  driverStandings: [
    for (var i = 1; i <= 20; i++)
      F1DriverStanding(entityId: '$i', name: i.isEven ? 'Max Emilian Verstappen' : 'Lando Norris', currentPoints: 350.5 - i, projectedPoints: 410.5 - i, championProbability: 0.31),
  ],
  constructorStandings: [
    for (var i = 1; i <= 10; i++)
      F1ConstructorStanding(entityId: '$i', name: i.isEven ? 'Oracle Red Bull Racing' : 'McLaren Formula 1 Team', currentPoints: 600.5 - i, projectedPoints: 700.5 - i, championProbability: 0.42),
  ],
);

Widget _page() => ProviderScope(
      overrides: [f1SeasonProjectionProvider.overrideWith((ref) async => _projection)],
      child: const MaterialApp(home: Scaffold(body: F1SeasonPage())),
    );

void main() {
  for (final width in mobileViewportWidths) {
    testWidgets('Drivers\' Championship renders with no overflow or clipped text at ${width}px wide', (tester) async {
      await pumpAtWidth(tester, width, _page());
      expect(tester.takeException(), isNull);
    });

    testWidgets('Constructors\' Championship renders with no overflow or clipped text at ${width}px wide', (tester) async {
      await pumpAtWidth(tester, width, _page());
      await tester.tap(find.text('Constructors\' Championship'));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      expect(truncatedText(tester), isEmpty);
    });
  }
}

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';

import 'package:front_end/core/models/sport_config.dart';
import 'package:front_end/core/theme/app_colors.dart';
import 'package:front_end/features/sport_shell/sport_shell_page.dart';

/// SportConfig.hasSeasonProjection (sport_config.dart) gates the Season tab.
/// Exercised directly via SportShellPage.sportConfigOverride, not by
/// depending on which real kSports entry currently has which flag -- that
/// coupling broke this test once already (ncaambb was the "no season
/// route" example until it got one).
const _configWithSeason = SportConfig(
  id: 'test-with-season',
  displayName: 'Test Sport',
  eventShape: EventShape.headToHead,
  accentColor: AppColors.cyan,
  active: true,
);

const _configWithoutSeason = SportConfig(
  id: 'test-without-season',
  displayName: 'Test Sport',
  eventShape: EventShape.headToHead,
  accentColor: AppColors.cyan,
  active: true,
  hasSeasonProjection: false,
);

void main() {
  Widget wrap(SportConfig config) {
    final router = GoRouter(
      initialLocation: '/${config.id}/events',
      routes: [
        GoRoute(
          path: '/${config.id}/events',
          builder: (context, state) =>
              SportShellPage(sportId: config.id, sportConfigOverride: config, child: const SizedBox()),
        ),
      ],
    );
    return MaterialApp.router(routerConfig: router);
  }

  testWidgets('shows the Season tab for a sport with a season projection', (tester) async {
    await tester.pumpWidget(wrap(_configWithSeason));
    expect(find.text('Season'), findsOneWidget);
  });

  testWidgets('hides the Season tab for a sport without one yet', (tester) async {
    await tester.pumpWidget(wrap(_configWithoutSeason));
    expect(find.text('Season'), findsNothing);
    expect(find.text('Events'), findsOneWidget);
    expect(find.text('Models'), findsOneWidget);
  });
}

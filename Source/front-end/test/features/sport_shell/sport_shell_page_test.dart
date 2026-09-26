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

const _configWithPerformance = SportConfig(
  id: 'test-with-performance',
  displayName: 'Test Sport',
  eventShape: EventShape.headToHead,
  accentColor: AppColors.cyan,
  active: true,
  hasPerformanceTab: true,
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

  group('Performance tab', () {
    testWidgets('is hidden unless the sport has a scorecard', (tester) async {
      await tester.pumpWidget(wrap(_configWithSeason));

      expect(find.text('Performance'), findsNothing);
    });

    testWidgets('is shown between Season and Models for a sport that has one', (tester) async {
      await tester.pumpWidget(wrap(_configWithPerformance));

      final season = tester.getCenter(find.text('Season')).dx;
      final performance = tester.getCenter(find.text('Performance')).dx;
      final models = tester.getCenter(find.text('Models')).dx;
      expect(season, lessThan(performance));
      expect(performance, lessThan(models));
    });

    testWidgets('tapping it goes to the sport performance route', (tester) async {
      final router = GoRouter(
        initialLocation: '/nfl/events',
        routes: [
          GoRoute(
            path: '/nfl/events',
            builder: (context, state) => SportShellPage(sportId: 'nfl', sportConfigOverride: _configWithPerformance, child: const SizedBox()),
          ),
          GoRoute(path: '/nfl/performance', builder: (context, state) => const Text('performance page')),
        ],
      );
      await tester.pumpWidget(MaterialApp.router(routerConfig: router));

      await tester.tap(find.text('Performance'));
      await tester.pumpAndSettle();

      expect(find.text('performance page'), findsOneWidget);
    });
  });

  test('every active sport has a Performance tab', () {
    for (final sport in kSports.where((s) => s.active)) {
      expect(sport.hasPerformanceTab, isTrue, reason: sport.id);
    }
  });
}

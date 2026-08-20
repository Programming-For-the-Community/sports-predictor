import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';

import 'package:front_end/features/sport_shell/sport_shell_page.dart';

/// SportConfig.hasSeasonProjection (sport_config.dart) gates the Season tab
/// -- nfl has a real /nfl/season route so it stays visible; ncaa_mbb has
/// no route at all, so it's hidden rather than linking to one that would
/// error.
void main() {
  Widget wrap(String sportId) {
    final router = GoRouter(
      initialLocation: '/$sportId/events',
      routes: [
        GoRoute(
          path: '/$sportId/events',
          builder: (context, state) => SportShellPage(sportId: sportId, child: const SizedBox()),
        ),
      ],
    );
    return MaterialApp.router(routerConfig: router);
  }

  testWidgets('shows the Season tab for a sport with a season projection', (tester) async {
    await tester.pumpWidget(wrap('nfl'));
    expect(find.text('Season'), findsOneWidget);
  });

  testWidgets('hides the Season tab for a sport without one yet', (tester) async {
    await tester.pumpWidget(wrap('ncaa_mbb'));
    expect(find.text('Season'), findsNothing);
    expect(find.text('Events'), findsOneWidget);
    expect(find.text('Models'), findsOneWidget);
  });
}

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';

import 'package:front_end/features/sport_shell/sport_shell_page.dart';

import '../../support/mobile_viewport.dart';

/// SportShellPage's top bar (back button + sport title + Events/Season/
/// Models toggle, sport_shell_page.dart:50) sits above every single page
/// in the app -- unlike the other mobile tests here, this needs a real
/// GoRouter (SportShellPage reads GoRouterState.of(context) for the
/// active-tab highlight) rather than a plain MaterialApp.
void main() {
  for (final width in mobileViewportWidths) {
    testWidgets('renders with no overflow at ${width}px wide', (tester) async {
      final router = GoRouter(
        initialLocation: '/nfl/events',
        routes: [
          GoRoute(
            path: '/nfl/events',
            builder: (context, state) => const SportShellPage(sportId: 'nfl', child: SizedBox()),
          ),
        ],
      );

      await pumpAtWidth(tester, width, MaterialApp.router(routerConfig: router));

      expect(tester.takeException(), isNull);
    });
  }
}

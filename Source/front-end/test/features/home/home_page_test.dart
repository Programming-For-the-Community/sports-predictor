import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:front_end/core/auth/auth_repository.dart';
import 'package:front_end/core/auth/cognito_auth_client.dart';
import 'package:front_end/core/data/live_scores_repository.dart';
import 'package:front_end/features/home/home_page.dart';

/// HomePage's sport cards (SportCard's own LIVE/ACTIVE dot) have no
/// per-sport Events page mounted to run that page's own live-scores poll
/// -- regression coverage for a real complaint ("leave the site open a
/// while... doesn't catch a sport going live") that a page-local poll
/// alone can't fix, since a user sitting on the home page never mounts
/// any Events page at all. Mirrors event_detail_page_test.dart's own
/// timer/resume poll tests.
void main() {
  setUp(() => SharedPreferences.setMockInitialValues({}));

  overrides({required void Function() onLiveScoresCall, required void Function() onPgaLiveScoresCall}) => [
        authRepositoryProvider.overrideWith(
          (ref) => AuthRepository(authClient: CognitoAuthClient(httpClient: MockClient((r) async => http.Response('{}', 200)))),
        ),
        liveScoresProvider.overrideWith((ref, sport) async {
          onLiveScoresCall();
          return const {};
        }),
        pgaLiveScoresProvider.overrideWith((ref, sport) async {
          onPgaLiveScoresCall();
          return const {};
        }),
      ];

  testWidgets('polls every active sport\'s live scores every 30s', (tester) async {
    var headToHeadCalls = 0;
    var pgaCalls = 0;

    await tester.pumpWidget(ProviderScope(
      overrides: overrides(onLiveScoresCall: () => headToHeadCalls++, onPgaLiveScoresCall: () => pgaCalls++),
      child: const MaterialApp(home: HomePage()),
    ));
    await tester.pumpAndSettle();
    final initialH2h = headToHeadCalls;
    final initialPga = pgaCalls;

    await tester.pump(const Duration(seconds: 31));
    await tester.pumpAndSettle();

    expect(headToHeadCalls, greaterThan(initialH2h));
    expect(pgaCalls, greaterThan(initialPga));
  });

  testWidgets('polls immediately on resume, not just on the 30s timer', (tester) async {
    var headToHeadCalls = 0;

    await tester.pumpWidget(ProviderScope(
      overrides: overrides(onLiveScoresCall: () => headToHeadCalls++, onPgaLiveScoresCall: () {}),
      child: const MaterialApp(home: HomePage()),
    ));
    await tester.pumpAndSettle();
    final initialCalls = headToHeadCalls;

    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    await tester.pumpAndSettle();

    expect(headToHeadCalls, greaterThan(initialCalls));
  });
}

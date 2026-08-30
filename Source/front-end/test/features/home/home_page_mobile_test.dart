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

import '../../support/mobile_viewport.dart';

/// HomePage's sport grid (SportCard, fixed-ideal-width 340) is the
/// simplest of this project's Wrap-of-fixed-width-card layouts -- no data
/// provider involved, kSports is a static list -- so a regression here
/// would point straight at core/widgets/responsive.dart's cardWidth()
/// itself rather than any one page's wiring of it. SportCard itself now
/// watches per-sport live-scores providers (for its LIVE/ACTIVE badge), so
/// both flavors are overridden here to keep this a pure layout test, not a
/// real network call.
void main() {
  setUp(() => SharedPreferences.setMockInitialValues({}));

  for (final width in mobileViewportWidths) {
    testWidgets('renders with no overflow at ${width}px wide', (tester) async {
      await pumpAtWidth(
        tester,
        width,
        ProviderScope(
          overrides: [
            authRepositoryProvider.overrideWith(
              (ref) => AuthRepository(authClient: CognitoAuthClient(httpClient: MockClient((r) async => http.Response('{}', 200)))),
            ),
            liveScoresProvider.overrideWith((ref, sport) async => const {}),
            pgaLiveScoresProvider.overrideWith((ref, sport) async => const {}),
          ],
          child: const MaterialApp(home: HomePage()),
        ),
      );

      expect(tester.takeException(), isNull);
    });
  }
}

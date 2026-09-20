import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:front_end/core/auth/auth_repository.dart';
import 'package:front_end/core/auth/cognito_auth_client.dart';
import 'package:front_end/core/data/live_scores_repository.dart';
import 'package:front_end/core/routing/app_router.dart';
import 'package:front_end/features/auth/login_page.dart';
import 'package:front_end/features/auth/splash_page.dart';
import 'package:front_end/features/home/home_page.dart';

/// Regression coverage for app_router.dart's own redirect logic -- a real
/// auth-flash bug was found and fixed here (a page's real content briefly
/// rendering before the redirect resolved). Drives real AuthRepository
/// instances through SharedPreferences + a mocked CognitoAuthClient (same
/// approach as auth_repository_test.dart) rather than faking AuthState
/// directly, since _authRedirect is file-private and only reachable
/// through the public appRouterProvider.
void main() {
  setUp(() => SharedPreferences.setMockInitialValues({}));

  baseOverrides(AuthRepository authRepo) => [
        authRepositoryProvider.overrideWith((ref) => authRepo),
        liveScoresProvider.overrideWith((ref, sport) async => const {}),
        pgaLiveScoresProvider.overrideWith((ref, sport) async => const {}),
        f1LiveScoresProvider.overrideWith((ref, sport) async => const {}),
      ];

  Widget wrap(AuthRepository authRepo) {
    return ProviderScope(
      overrides: baseOverrides(authRepo),
      child: Consumer(
        builder: (context, ref, _) => MaterialApp.router(routerConfig: ref.watch(appRouterProvider)),
      ),
    );
  }

  testWidgets('still-restoring session shows the splash page, not real content', (tester) async {
    // A never-completing refresh (isNearExpiry token, stuck HTTP call) keeps
    // AuthRepository in AuthInitial for the whole test -- restore is awaiting
    // a response that never arrives.
    SharedPreferences.setMockInitialValues({
      'cognito_tokens': jsonEncodeTokens(expiresIn: 30), // near expiry -> triggers a refresh
    });
    final stuck = Completer<http.Response>();
    final authRepo = AuthRepository(authClient: CognitoAuthClient(httpClient: MockClient((r) => stuck.future)));

    await tester.pumpWidget(wrap(authRepo));
    await tester.pump();

    expect(find.byType(SplashPage), findsOneWidget);
    expect(find.byType(HomePage), findsNothing);

    // Resolve the stuck refresh so CognitoAuthClient's own internal request
    // timeout timer doesn't outlive the test.
    stuck.complete(http.Response('{}', 401));
    await tester.pump();
  });

  testWidgets('unauthenticated visiting home is redirected to login', (tester) async {
    final authRepo = AuthRepository(authClient: CognitoAuthClient(httpClient: MockClient((r) async => http.Response('{}', 200))));

    await tester.pumpWidget(wrap(authRepo));
    await tester.pumpAndSettle();

    expect(find.byType(LoginPage), findsOneWidget);
    expect(find.byType(HomePage), findsNothing);
  });

  testWidgets('authenticated visiting home stays on home', (tester) async {
    SharedPreferences.setMockInitialValues({'cognito_tokens': jsonEncodeTokens(expiresIn: 3600)});
    final authRepo = AuthRepository(authClient: CognitoAuthClient(httpClient: MockClient((r) async => http.Response('{}', 200))));

    await tester.pumpWidget(wrap(authRepo));
    await tester.pumpAndSettle();

    expect(find.byType(HomePage), findsOneWidget);
    expect(find.byType(LoginPage), findsNothing);
  });

  testWidgets('signing in from the login page redirects to home', (tester) async {
    final authRepo = AuthRepository(
      authClient: CognitoAuthClient(httpClient: MockClient((r) async {
        return http.Response(
          '{"AuthenticationResult": {"AccessToken": "a", "IdToken": "i", "RefreshToken": "r", "ExpiresIn": 3600}}',
          200,
        );
      })),
    );
    await tester.pumpWidget(wrap(authRepo));
    await tester.pumpAndSettle();
    expect(find.byType(LoginPage), findsOneWidget);

    await authRepo.login(username: 'chamar', password: 'hunter2');
    await tester.pumpAndSettle();

    expect(find.byType(HomePage), findsOneWidget);
    expect(find.byType(LoginPage), findsNothing);
  });

  testWidgets('signing out from home redirects back to login', (tester) async {
    SharedPreferences.setMockInitialValues({'cognito_tokens': jsonEncodeTokens(expiresIn: 3600)});
    final authRepo = AuthRepository(authClient: CognitoAuthClient(httpClient: MockClient((r) async => http.Response('{}', 200))));
    await tester.pumpWidget(wrap(authRepo));
    await tester.pumpAndSettle();
    expect(find.byType(HomePage), findsOneWidget);

    await authRepo.logout();
    await tester.pumpAndSettle();

    expect(find.byType(LoginPage), findsOneWidget);
    expect(find.byType(HomePage), findsNothing);
  });
}

String jsonEncodeTokens({required int expiresIn}) {
  final expiresAt = DateTime.now().add(Duration(seconds: expiresIn));
  return '{"accessToken":"a","idToken":"i","refreshToken":"r","expiresAt":"${expiresAt.toIso8601String()}"}';
}

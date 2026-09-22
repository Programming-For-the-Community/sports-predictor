import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:front_end/core/auth/auth_repository.dart';
import 'package:front_end/core/auth/cognito_auth_client.dart';
import 'package:front_end/core/data/events_repository.dart';
import 'package:front_end/core/data/f1_events_repository.dart';
import 'package:front_end/core/data/f1_season_repository.dart';
import 'package:front_end/core/data/field_events_repository.dart';
import 'package:front_end/core/data/live_scores_repository.dart';
import 'package:front_end/core/data/models_repository.dart';
import 'package:front_end/core/data/pga_season_repository.dart';
import 'package:front_end/core/data/season_repository.dart';
import 'package:front_end/core/models/sport_config.dart';
import 'package:front_end/core/routing/app_router.dart';
import 'package:front_end/core/routing/app_routes.dart';
import 'package:front_end/features/auth/login_page.dart';
import 'package:front_end/features/auth/splash_page.dart';
import 'package:front_end/features/events/event_detail_page.dart';
import 'package:front_end/features/events/event_list_page.dart';
import 'package:front_end/features/events/f1_event_detail_page.dart';
import 'package:front_end/features/events/f1_event_list_page.dart';
import 'package:front_end/features/events/field_event_detail_page.dart';
import 'package:front_end/features/events/field_event_list_page.dart';
import 'package:front_end/features/home/home_page.dart';
import 'package:front_end/features/models/model_cards_page.dart';
import 'package:front_end/features/season/f1_season_page.dart';
import 'package:front_end/features/season/pga_season_page.dart';
import 'package:front_end/features/season/season_page.dart';

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

  group('sport-scoped routes', () {
    // Regression coverage for _eventListPageFor/_eventDetailPageFor/
    // _seasonPageFor -- the router's own sport-branching logic (f1 gets
    // its own widget, field-shaped sports share one, head-to-head sports
    // share another) is real dispatch logic worth locking in on its own,
    // separately from whatever each destination page renders once there
    // (that's each page's own test file's job). Every data-fetching
    // provider a destination page might read is overridden to something
    // that resolves without crashing -- an empty list for list-shaped
    // providers, or a rejected future for single-object providers (an
    // AsyncError is enough for a page to still mount its own error/empty
    // state, and its exact content isn't what these tests check) -- so
    // this suite exercises real production api_client.dart code with zero
    // real HTTP calls, without needing to fabricate a realistic response
    // shape for every page this router can reach.
    ProviderContainer buildRouterContainer() {
      SharedPreferences.setMockInitialValues({'cognito_tokens': jsonEncodeTokens(expiresIn: 3600)});
      final authRepo = AuthRepository(authClient: CognitoAuthClient(httpClient: MockClient((r) async => http.Response('{}', 200))));
      return ProviderContainer(overrides: [
        authRepositoryProvider.overrideWith((ref) => authRepo),
        liveScoresProvider.overrideWith((ref, sport) async => const {}),
        pgaLiveScoresProvider.overrideWith((ref, sport) async => const {}),
        f1LiveScoresProvider.overrideWith((ref, sport) async => const {}),
        fieldLiveScoresProvider.overrideWith((ref, sport) async => const {}),
        eventsListProvider.overrideWith((ref, query) async => const []),
        f1EventsListProvider.overrideWith((ref, query) async => const []),
        fieldEventsListProvider.overrideWith((ref, query) async => const []),
        modelsListProvider.overrideWith((ref, sport) async => const []),
        eventPredictionProvider.overrideWith((ref, query) async => throw StateError('not needed for this test')),
        f1EventPredictionProvider.overrideWith((ref, query) async => throw StateError('not needed for this test')),
        fieldEventPredictionProvider.overrideWith((ref, query) async => throw StateError('not needed for this test')),
        seasonProjectionProvider.overrideWith((ref, sport) async => throw StateError('not needed for this test')),
        f1SeasonProjectionProvider.overrideWith((ref) async => throw StateError('not needed for this test')),
        pgaSeasonProjectionProvider.overrideWith((ref) async => throw StateError('not needed for this test')),
      ]);
    }

    Future<void> pumpAtRoute(WidgetTester tester, ProviderContainer container, String path) async {
      await tester.pumpWidget(UncontrolledProviderScope(
        container: container,
        child: Consumer(builder: (context, ref, _) => MaterialApp.router(routerConfig: ref.watch(appRouterProvider))),
      ));
      await tester.pumpAndSettle(); // settles the initial home route's own auth redirect
      container.read(appRouterProvider).go(path);
      await tester.pumpAndSettle();
    }

    testWidgets('a head-to-head sports events route renders EventListPage', (tester) async {
      final container = buildRouterContainer();
      addTearDown(container.dispose);
      await pumpAtRoute(tester, container, AppRoutes.events(SportIds.nfl));
      expect(find.byType(EventListPage), findsOneWidget);
    });

    testWidgets('a field-shaped sports events route renders FieldEventListPage', (tester) async {
      final container = buildRouterContainer();
      addTearDown(container.dispose);
      await pumpAtRoute(tester, container, AppRoutes.events(SportIds.pga));
      expect(find.byType(FieldEventListPage), findsOneWidget);
    });

    testWidgets('f1s events route renders its own F1EventListPage, not FieldEventListPage', (tester) async {
      // F1 shares EventShape.field with PGA but has its own distinct
      // page -- the real reason _eventListPageFor checks sportId by id
      // first, not eventShape alone.
      final container = buildRouterContainer();
      addTearDown(container.dispose);
      await pumpAtRoute(tester, container, AppRoutes.events(SportIds.f1));
      expect(find.byType(F1EventListPage), findsOneWidget);
      expect(find.byType(FieldEventListPage), findsNothing);
    });

    testWidgets('a head-to-head sports event detail route renders EventDetailPage', (tester) async {
      final container = buildRouterContainer();
      addTearDown(container.dispose);
      await pumpAtRoute(tester, container, AppRoutes.eventDetail(SportIds.nfl, '401547417'));
      expect(find.byType(EventDetailPage), findsOneWidget);
    });

    testWidgets('a field-shaped sports event detail route renders FieldEventDetailPage', (tester) async {
      final container = buildRouterContainer();
      addTearDown(container.dispose);
      await pumpAtRoute(tester, container, AppRoutes.eventDetail(SportIds.pga, '401811963'));
      expect(find.byType(FieldEventDetailPage), findsOneWidget);
    });

    testWidgets('f1s event detail route renders its own F1EventDetailPage, not FieldEventDetailPage', (tester) async {
      final container = buildRouterContainer();
      addTearDown(container.dispose);
      await pumpAtRoute(tester, container, AppRoutes.eventDetail(SportIds.f1, '1197'));
      expect(find.byType(F1EventDetailPage), findsOneWidget);
      expect(find.byType(FieldEventDetailPage), findsNothing);
    });

    testWidgets('the models route renders ModelCardsPage for any sport', (tester) async {
      final container = buildRouterContainer();
      addTearDown(container.dispose);
      await pumpAtRoute(tester, container, AppRoutes.models(SportIds.nfl));
      expect(find.byType(ModelCardsPage), findsOneWidget);
    });

    testWidgets('a plain head-to-head sports season route renders SeasonPage', (tester) async {
      final container = buildRouterContainer();
      addTearDown(container.dispose);
      await pumpAtRoute(tester, container, AppRoutes.season(SportIds.nfl));
      expect(find.byType(SeasonPage), findsOneWidget);
    });

    testWidgets('pgas season route renders its own PgaSeasonPage, not SeasonPage', (tester) async {
      final container = buildRouterContainer();
      addTearDown(container.dispose);
      await pumpAtRoute(tester, container, AppRoutes.season(SportIds.pga));
      expect(find.byType(PgaSeasonPage), findsOneWidget);
      expect(find.byType(SeasonPage), findsNothing);
    });

    testWidgets('f1s season route renders its own F1SeasonPage, not PgaSeasonPage or SeasonPage', (tester) async {
      final container = buildRouterContainer();
      addTearDown(container.dispose);
      await pumpAtRoute(tester, container, AppRoutes.season(SportIds.f1));
      expect(find.byType(F1SeasonPage), findsOneWidget);
      expect(find.byType(PgaSeasonPage), findsNothing);
      expect(find.byType(SeasonPage), findsNothing);
    });
  });
}

String jsonEncodeTokens({required int expiresIn}) {
  final expiresAt = DateTime.now().add(Duration(seconds: expiresIn));
  return '{"accessToken":"a","idToken":"i","refreshToken":"r","expiresAt":"${expiresAt.toIso8601String()}"}';
}

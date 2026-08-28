import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../auth/auth_repository.dart';
import '../data/events_repository.dart';
import '../data/field_events_repository.dart';
import '../data/models_repository.dart';
import '../data/season_repository.dart';
import '../models/sport_config.dart';
import '../../features/auth/login_page.dart';
import '../../features/auth/splash_page.dart';
import '../../features/home/home_page.dart';
import '../../features/events/event_detail_page.dart';
import '../../features/events/event_list_page.dart';
import '../../features/events/field_event_detail_page.dart';
import '../../features/events/field_event_list_page.dart';
import '../../features/models/model_cards_page.dart';
import '../../features/season/pga_season_page.dart';
import '../../features/season/season_page.dart';
import '../../features/sport_shell/sport_shell_page.dart';

/// Bridges Riverpod's authRepositoryProvider state into a Listenable so
/// go_router's `refreshListenable` re-evaluates `redirect` whenever auth
/// state changes (login, logout, or a session restore completing) --
/// standard Riverpod+go_router integration pattern.
///
/// Also invalidates every data provider on a genuine sign-in (transitioning
/// into AuthAuthenticated from some other state). None of the data
/// providers watch auth state themselves, so a session dying mid-use
/// leaves them permanently cached on that error; this is what tells them
/// to forget the stale failure and retry.
///
/// Deliberately not invalidated on every state change -- a routine
/// proactive token refresh also reassigns state (same signed-in session,
/// nothing about the data went stale), and resetting an actively-watched
/// provider mid-fetch on every one of those would fight with whatever page
/// is currently loading.
class _AuthChangeNotifier extends ChangeNotifier {
  _AuthChangeNotifier(Ref ref) {
    ref.listen<AuthState>(authRepositoryProvider, (previous, next) {
      final signedIn = next is AuthAuthenticated && previous is! AuthAuthenticated;
      if (signedIn) {
        ref.invalidate(eventsListProvider);
        ref.invalidate(eventPredictionProvider);
        ref.invalidate(fieldEventsListProvider);
        ref.invalidate(fieldEventPredictionProvider);
        ref.invalidate(modelsListProvider);
        ref.invalidate(seasonProjectionProvider);
      }
      notifyListeners();
    });
  }
}

final appRouterProvider = Provider<GoRouter>((ref) {
  final authNotifier = _AuthChangeNotifier(ref);

  return GoRouter(
    initialLocation: '/',
    refreshListenable: authNotifier,
    redirect: (context, state) {
      final authState = ref.read(authRepositoryProvider);
      final loggingIn = state.matchedLocation == '/login';
      final onSplash = state.matchedLocation == '/splash';

      // Session restore hasn't finished -- send every route here, not
      // just '/', so the originally-requested route's real content never
      // builds and flashes on screen before this redirect resolves.
      if (authState is AuthInitial) return onSplash ? null : '/splash';

      final authenticated = authState is AuthAuthenticated;
      if (!authenticated) return loggingIn ? null : '/login';
      return (loggingIn || onSplash) ? '/' : null;
    },
    routes: [
      GoRoute(path: '/login', builder: (context, state) => const LoginPage()),
      GoRoute(path: '/splash', builder: (context, state) => const SplashPage()),
      GoRoute(path: '/', builder: (context, state) => const HomePage()),
      ShellRoute(
        builder: (context, state, child) {
          final sportId = state.pathParameters['sport']!;
          return SportShellPage(sportId: sportId, child: child);
        },
        routes: [
          GoRoute(
            path: '/:sport/events',
            // Router-level branch on eventShape -- keeps EventListPage/
            // EventDetailPage/GameRow completely untouched, mirroring how
            // SportCard already branches on eventShape at the leaf-widget
            // level (sport_card.dart). Same path template either way, so
            // this can't be two separate GoRoutes.
            builder: (context, state) {
              final sportId = state.pathParameters['sport']!;
              return sportById(sportId).eventShape == EventShape.field
                  ? FieldEventListPage(sportId: sportId)
                  : EventListPage(sportId: sportId);
            },
            routes: [
              GoRoute(
                path: ':eventId',
                builder: (context, state) {
                  final sportId = state.pathParameters['sport']!;
                  final eventId = state.pathParameters['eventId']!;
                  return sportById(sportId).eventShape == EventShape.field
                      ? FieldEventDetailPage(sportId: sportId, eventId: eventId)
                      : EventDetailPage(sportId: sportId, eventId: eventId);
                },
              ),
            ],
          ),
          GoRoute(
            path: '/:sport/models',
            builder: (context, state) => ModelCardsPage(sportId: state.pathParameters['sport']!),
          ),
          GoRoute(
            path: '/:sport/season',
            builder: (context, state) {
              final sportId = state.pathParameters['sport']!;
              return sportById(sportId).usesFedexCupSeasonPage
                  ? const PgaSeasonPage()
                  : SeasonPage(sportId: sportId);
            },
          ),
        ],
      ),
    ],
  );
});

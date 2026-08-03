import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../auth/auth_repository.dart';
import '../../features/auth/login_page.dart';
import '../../features/home/home_page.dart';
import '../../features/events/event_detail_page.dart';
import '../../features/events/event_list_page.dart';
import '../../features/models/model_cards_page.dart';
import '../../features/season/season_page.dart';
import '../../features/sport_shell/sport_shell_page.dart';

/// Bridges Riverpod's authRepositoryProvider state into a Listenable so
/// go_router's `refreshListenable` re-evaluates `redirect` whenever auth
/// state changes (login, logout, or a session restore completing) --
/// standard Riverpod+go_router integration pattern.
class _AuthChangeNotifier extends ChangeNotifier {
  _AuthChangeNotifier(Ref ref) {
    ref.listen<AuthState>(authRepositoryProvider, (previous, next) => notifyListeners());
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

      // Session restore (reading persisted tokens, possibly refreshing
      // them) hasn't finished -- don't make a routing decision on stale
      // information.
      if (authState is AuthInitial) return null;

      final authenticated = authState is AuthAuthenticated;
      if (!authenticated) return loggingIn ? null : '/login';
      return loggingIn ? '/' : null;
    },
    routes: [
      GoRoute(path: '/login', builder: (context, state) => const LoginPage()),
      GoRoute(path: '/', builder: (context, state) => const HomePage()),
      ShellRoute(
        builder: (context, state, child) {
          final sportId = state.pathParameters['sport']!;
          return SportShellPage(sportId: sportId, child: child);
        },
        routes: [
          GoRoute(
            path: '/:sport/events',
            builder: (context, state) => EventListPage(sportId: state.pathParameters['sport']!),
            routes: [
              GoRoute(
                path: ':eventId',
                builder: (context, state) => EventDetailPage(
                  sportId: state.pathParameters['sport']!,
                  eventId: state.pathParameters['eventId']!,
                ),
              ),
            ],
          ),
          GoRoute(
            path: '/:sport/models',
            builder: (context, state) => ModelCardsPage(sportId: state.pathParameters['sport']!),
          ),
          GoRoute(
            path: '/:sport/season',
            builder: (context, state) => SeasonPage(sportId: state.pathParameters['sport']!),
          ),
        ],
      ),
    ],
  );
});

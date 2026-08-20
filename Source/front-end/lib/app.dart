import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'core/auth/auth_repository.dart';
import 'core/routing/app_router.dart';
import 'core/theme/app_theme.dart';

class App extends ConsumerWidget {
  const App({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final router = ref.watch(appRouterProvider);
    return MaterialApp.router(
      title: 'sports-predictor',
      debugShowCheckedModeBanner: false,
      theme: AppTheme.dark,
      routerConfig: router,
      // Wraps every routed page -- onPointerDown covers taps/clicks/the
      // start of any drag or touch-scroll, onPointerSignal covers
      // mouse-wheel/trackpad scroll, resetting AuthRepository's inactivity
      // clock on any real user interaction.
      builder: (context, child) => Listener(
        onPointerDown: (_) => ref.read(authRepositoryProvider.notifier).recordActivity(),
        onPointerSignal: (_) => ref.read(authRepositoryProvider.notifier).recordActivity(),
        child: child,
      ),
    );
  }
}

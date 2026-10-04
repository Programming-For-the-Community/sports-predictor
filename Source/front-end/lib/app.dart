import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:home_widget/home_widget.dart';

import 'core/auth/auth_repository.dart';
import 'core/mobile/app_shell.dart';
import 'core/mobile/local_notifications.dart';
import 'core/mobile/widget_data.dart';
import 'core/mobile/widget_sync.dart';
import 'core/routing/app_router.dart';
import 'core/routing/app_routes.dart';
import 'core/theme/app_theme.dart';

class App extends ConsumerStatefulWidget {
  const App({super.key});

  @override
  ConsumerState<App> createState() => _AppState();
}

class _AppState extends ConsumerState<App> with WidgetsBindingObserver {
  @override
  void initState() {
    super.initState();
    if (ref.read(appShellProvider) == AppShell.androidApp) {
      WidgetsBinding.instance.addObserver(this);
      _listenForNotificationTaps();
      _listenForWidgetLaunches();
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  // Launch is covered by AuthRepository's own restore, which also renews.
  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) ref.read(authRepositoryProvider.notifier).renewSession();
  }

  Future<void> _listenForNotificationTaps() async {
    final notifier = ref.read(deviceNotifierProvider);
    await notifier.initialize(onTap: _openWhenSignedIn);
    final launchRoute = await notifier.launchRoute();
    if (launchRoute != null) _openWhenSignedIn(launchRoute);
  }

  Future<void> _listenForWidgetLaunches() async {
    HomeWidget.widgetClicked.listen((uri) {
      final route = routeFromWidgetUri(uri);
      if (route != null) _openWhenSignedIn(route);
    });
    final launchRoute = routeFromWidgetUri(await HomeWidget.initiallyLaunchedFromHomeWidget());
    if (launchRoute != null) _openWhenSignedIn(launchRoute);

    // Android launched WidgetConfigureActivity for a newly placed widget.
    final configureId = int.tryParse(await HomeWidget.initiallyLaunchedFromHomeWidgetConfigure() ?? '');
    if (configureId == null) return;
    final placed = await const DeviceWidgetHost().placedWidgets();
    final widget = placed.where((w) => w.id == configureId).firstOrNull;
    if (widget != null) _openWhenSignedIn(AppRoutes.widgetSetup(configureId, widget.kind.name));
  }

  /// A tap that cold-starts the app lands before the session is restored;
  /// navigating then would be overridden by the splash/login redirect.
  void _openWhenSignedIn(String route) {
    if (ref.read(authRepositoryProvider) is AuthAuthenticated) {
      ref.read(appRouterProvider).go(route);
      return;
    }
    late final ProviderSubscription<AuthState> subscription;
    subscription = ref.listenManual<AuthState>(authRepositoryProvider, (previous, next) {
      if (next is! AuthAuthenticated) return;
      subscription.close();
      // After the redirect's own navigation to Home settles.
      WidgetsBinding.instance.addPostFrameCallback((_) => ref.read(appRouterProvider).go(route));
    });
  }

  @override
  Widget build(BuildContext context) {
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

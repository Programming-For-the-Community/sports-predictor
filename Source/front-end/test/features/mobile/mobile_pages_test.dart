import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_riverpod/misc.dart' show Override;
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:ota_update/ota_update.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:front_end/core/api/api_client.dart';
import 'package:front_end/core/auth/auth_repository.dart';
import 'package:front_end/core/auth/cognito_auth_client.dart';
import 'package:front_end/core/data/live_scores_repository.dart';
import 'package:front_end/core/mobile/app_release.dart';
import 'package:front_end/core/mobile/app_shell.dart';
import 'package:front_end/core/mobile/app_updates.dart';
import 'package:front_end/core/mobile/local_notifications.dart';
import 'package:front_end/core/mobile/widget_data.dart';
import 'package:front_end/core/mobile/widget_sync.dart';
import 'package:front_end/features/home/home_page.dart';
import 'package:front_end/features/mobile/settings_pages.dart';
import 'package:front_end/features/mobile/widget_setup_page.dart';

import '../../support/api_client_test_support.dart';

const _release = {
  'x-amz-meta-version-code': '47',
  'x-amz-meta-version-name': '1.1.0',
  'x-amz-meta-sha256': 'abc123def456',
  'x-amz-meta-notes': 'Faster startup',
  'content-length': '18979225',
};

class _QuietNotifier extends DeviceNotifier {
  var permissionRequests = 0;

  @override
  Future<void> requestPermission() async => permissionRequests++;
}

class _FakeInstaller implements AppUpdateInstaller {
  final controller = StreamController<OtaEvent>();
  AppRelease? installed;
  String? username;

  @override
  Stream<OtaEvent> install(AppRelease release, {String? username}) {
    installed = release;
    this.username = username;
    return controller.stream;
  }
}

class _FakeWidgetHost implements WidgetHost {
  final sports = <int, String>{};
  final saved = <String, Map<String, Object?>>{};
  final redrawn = <HomeWidgetKind>[];

  @override
  Future<List<({int id, HomeWidgetKind kind})>> placedWidgets() async => const [];

  @override
  Future<String?> sportFor(int widgetId) async => sports[widgetId];

  @override
  Future<void> setSport(int widgetId, String sportId) async => sports[widgetId] = sportId;

  @override
  Future<void> save(String key, Map<String, Object?> data) async => saved[key] = data;

  @override
  Future<void> redraw(HomeWidgetKind kind) async => redrawn.add(kind);
}

List<Override> _overrides({required AppShell shell, Map<String, String> releaseHeaders = _release, int installedCode = 41, DeviceNotifier? notifier}) => [
      appShellProvider.overrideWithValue(shell),
      authRepositoryProvider.overrideWith(
        (ref) => AuthRepository(authClient: CognitoAuthClient(httpClient: MockClient((r) async => http.Response('{}', 200)))),
      ),
      liveScoresProvider.overrideWith((ref, sport) async => const {}),
      pgaLiveScoresProvider.overrideWith((ref, sport) async => const {}),
      appReleaseClientProvider.overrideWithValue(AppReleaseClient(httpClient: MockClient((r) async => http.Response('', 200, headers: releaseHeaders)))),
      installedVersionProvider.overrideWith((ref) async => InstalledVersion(versionCode: installedCode, versionName: '1.0.0')),
      deviceNotifierProvider.overrideWithValue(notifier ?? _QuietNotifier()),
    ];

Future<void> _pump(WidgetTester tester, Widget page, List<Override> overrides) async {
  tester.view.physicalSize = const Size(400, 1600);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(ProviderScope(overrides: overrides, child: MaterialApp(home: page)));
  await tester.pumpAndSettle();
}

void main() {
  setUp(() => SharedPreferences.setMockInitialValues({}));

  group('Home in an Android browser', () {
    testWidgets('has a Get app button that opens the install sheet', (tester) async {
      await _pump(tester, const HomePage(), _overrides(shell: AppShell.androidBrowser));

      await tester.tap(find.text('Get app'));
      await tester.pumpAndSettle();

      expect(find.text('Install on Android'), findsOneWidget);
      expect(find.text('Download APK · 18.1 MB'), findsOneWidget);
    });

    testWidgets('shows no button when no APK is published', (tester) async {
      await _pump(tester, const HomePage(), _overrides(shell: AppShell.androidBrowser, releaseHeaders: const {}));

      expect(find.text('Get app'), findsNothing);
    });
  });

  testWidgets('Home on desktop web shows neither the offer nor the update banner', (tester) async {
    await _pump(tester, const HomePage(), _overrides(shell: AppShell.web));

    expect(find.text('Get app'), findsNothing);
    expect(find.textContaining('is ready'), findsNothing);
    expect(find.text('Sign out'), findsOneWidget);
  });

  group('Home in the Android app', () {
    testWidgets('asks for notification permission and shows a pending update', (tester) async {
      final notifier = _QuietNotifier();
      await _pump(tester, const HomePage(), _overrides(shell: AppShell.androidApp, notifier: notifier));

      expect(notifier.permissionRequests, 1);
      expect(find.text('Sign out'), findsNothing);
      expect(find.byTooltip('Settings'), findsOneWidget);
      expect(find.textContaining('v1.1.0 is ready.'), findsOneWidget);
    });

    testWidgets('"Later" hides the banner for a day', (tester) async {
      await _pump(tester, const HomePage(), _overrides(shell: AppShell.androidApp));

      await tester.tap(find.text('Later'));
      await tester.pumpAndSettle();

      expect(find.textContaining('is ready'), findsNothing);
    });

    testWidgets('no banner when the installed build is current', (tester) async {
      await _pump(tester, const HomePage(), _overrides(shell: AppShell.androidApp, installedCode: 47));

      expect(find.textContaining('is ready'), findsNothing);
    });
  });

  testWidgets('Notifications page saves each sport\'s toggle', (tester) async {
    await _pump(tester, const NotificationSettingsPage(), _overrides(shell: AppShell.androidApp));

    expect(find.text('Weekly model report'.toUpperCase()), findsOneWidget);
    await tester.tap(find.byType(SwitchListTile).first);
    await tester.pumpAndSettle();

    final prefs = await SharedPreferences.getInstance();
    expect(prefs.getBool('notify_report_nfl'), isFalse);
    expect(tester.widget<SwitchListTile>(find.byType(SwitchListTile).first).value, isFalse);
  });

  group('App updates page', () {
    testWidgets('installs the pending release and reports progress', (tester) async {
      final installer = _FakeInstaller();
      await _pump(tester, const AppUpdatesPage(), [
        ..._overrides(shell: AppShell.androidApp),
        appUpdateInstallerProvider.overrideWithValue(installer),
      ]);

      expect(find.text('v1.0.0 (41)'), findsOneWidget);
      expect(find.text('v1.1.0 (47)'), findsOneWidget);
      expect(find.text('Faster startup'), findsOneWidget);

      await tester.tap(find.text('Download & install'));
      await tester.pump();
      installer.controller.add(OtaEvent(OtaStatus.DOWNLOADING, '50'));
      await tester.pump();
      expect(installer.installed?.versionCode, 47);
      // Signed out in this test, so the download isn't tagged with a user.
      expect(installer.username, isNull);
      expect(find.text('Downloading… 9.1 MB / 18.1 MB'), findsOneWidget);

      installer.controller.add(OtaEvent(OtaStatus.INSTALLING, null));
      await tester.pump();
      await tester.pump();
      expect(find.text('Opening Android\'s installer. Confirm the install there.'), findsOneWidget);
    });

    testWidgets('says so when already current', (tester) async {
      await _pump(tester, const AppUpdatesPage(), _overrides(shell: AppShell.androidApp, installedCode: 47));

      expect(find.text('You\'re on the latest version.'), findsOneWidget);
      expect(find.text('Download & install'), findsNothing);
    });
  });

  test('installStatusMessage explains a missing install permission', () {
    expect(installStatusMessage(OtaEvent(OtaStatus.PERMISSION_NOT_GRANTED_ERROR, null), null), contains('Install unknown apps'));
    expect(installStatusMessage(OtaEvent(OtaStatus.CHECKSUM_ERROR, null), null), contains('Check again'));
  });

  testWidgets('Widget setup saves the chosen sport, loads its data, and finishes configuring', (tester) async {
    final host = _FakeWidgetHost();
    var finished = false;
    final api = buildTestApiClient((request) async => http.Response(
          jsonEncode({
            'sport': 'nba',
            'season': 2026,
            'period_kind': 'week',
            'models': [
              {
                'model_name': 'win-probability',
                'version': 4,
                'kind': 'pick',
                'band_kind': 'confidence',
                'season': {'value': 0.66, 'n': 50},
                'bands': [],
              },
            ],
          }),
          200,
        ));
    await _pump(tester, const WidgetSetupPage(widgetId: 12, kind: HomeWidgetKind.accuracy), [
      ..._overrides(shell: AppShell.androidApp),
      widgetHostProvider.overrideWithValue(host),
      finishWidgetConfigureProvider.overrideWithValue(() async => finished = true),
      apiClientProvider.overrideWithValue(api),
    ]);

    await tester.tap(find.text('NBA'));
    // The spinner stays up until Android closes the configure activity.
    for (var i = 0; i < 5; i++) {
      await tester.pump(const Duration(milliseconds: 50));
    }

    expect(host.sports[12], 'nba');
    expect(host.saved['accuracy_nba']?['season_pct'], 0.66);
    expect(host.redrawn, [HomeWidgetKind.accuracy]);
    expect(finished, isTrue);
  });
}

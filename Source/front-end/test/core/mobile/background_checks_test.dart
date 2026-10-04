import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:front_end/core/auth/cognito_auth_client.dart';
import 'package:front_end/core/auth/token_store.dart';
import 'package:front_end/core/mobile/app_release.dart';
import 'package:front_end/core/mobile/background_checks.dart';
import 'package:front_end/core/mobile/local_notifications.dart';
import 'package:front_end/core/mobile/widget_data.dart';
import 'package:front_end/core/mobile/widget_sync.dart';
import 'package:front_end/core/models/sport_config.dart';
import 'package:front_end/core/routing/app_routes.dart';

class _MemoryTokenStore implements TokenStore {
  _MemoryTokenStore([this.value]);

  String? value;

  @override
  Future<String?> read() async => value;

  @override
  Future<void> write(String tokensJson) async => value = tokensJson;

  @override
  Future<void> delete() async => value = null;
}

class _Shown {
  _Shown(this.id, this.channel, this.title, this.body, this.route);

  final int id;
  final NotificationChannel channel;
  final String title;
  final String body;
  final String route;
}

class _RecordingNotifier implements LocalNotifier {
  final shown = <_Shown>[];

  @override
  Future<void> show({required int id, required NotificationChannel channel, required String title, required String body, required String route}) async {
    shown.add(_Shown(id, channel, title, body, route));
  }
}

class _FakeWidgetHost implements WidgetHost {
  _FakeWidgetHost([this.placed = const []]);

  final List<({int id, HomeWidgetKind kind})> placed;
  final sports = <int, String>{};
  final saved = <String, Map<String, Object?>>{};
  final redrawn = <HomeWidgetKind>[];

  @override
  Future<List<({int id, HomeWidgetKind kind})>> placedWidgets() async => placed;

  @override
  Future<String?> sportFor(int widgetId) async => sports[widgetId];

  @override
  Future<void> setSport(int widgetId, String sportId) async => sports[widgetId] = sportId;

  @override
  Future<void> save(String key, Map<String, Object?> data) async => saved[key] = data;

  @override
  Future<void> redraw(HomeWidgetKind kind) async => redrawn.add(kind);
}

String _tokens({Duration expiresIn = const Duration(hours: 1)}) => jsonEncode(
      CognitoTokens(accessToken: 'access', idToken: 'id-token', refreshToken: 'refresh', expiresAt: DateTime.now().add(expiresIn)).toJson(),
    );

String _scorecard(String sport, {String? label = 'Wk 5'}) => jsonEncode({
      'sport': sport,
      'season': 2026,
      'period_kind': 'week',
      'models': [
        {
          'model_name': 'win-probability',
          'version': 9,
          'kind': 'pick',
          'band_kind': 'confidence',
          'season': {'value': 0.68, 'n': 32},
          'last_period': label == null ? null : {'value': 0.75, 'n': 12, 'label': label},
          'bands': [],
        },
      ],
    });

// Wednesday 11 AM Eastern: NFL's slot was an hour ago; F1's (Tuesday) is
// still inside the grace period, PGA's (Monday) just past it.
final _wednesdayMorning = DateTime.utc(2026, 10, 7, 15);

void main() {
  late SharedPreferences prefs;
  late _RecordingNotifier notifier;
  late List<http.Request> requests;

  setUp(() async {
    SharedPreferences.setMockInitialValues({});
    prefs = await SharedPreferences.getInstance();
    notifier = _RecordingNotifier();
    requests = [];
  });

  http.Client client({int publishedVersionCode = 47, Map<String, String> scorecards = const {}, int scorecardStatus = 200}) =>
      MockClient((request) async {
        requests.add(request);
        if (request.method == 'HEAD') {
          return http.Response('', 200, headers: {
            'x-amz-meta-version-code': '$publishedVersionCode',
            'x-amz-meta-version-name': '1.1.0',
            'x-amz-meta-sha256': 'abc',
          });
        }
        if (request.url.host.startsWith('cognito-idp')) {
          return http.Response(jsonEncode({'AuthenticationResult': {'AccessToken': 'a2', 'IdToken': 'id-2', 'ExpiresIn': 3600}}), 200);
        }
        final sport = request.url.pathSegments.first;
        return http.Response(scorecards[sport] ?? _scorecard(sport, label: null), scorecardStatus);
      });

  Future<void> run(http.Client httpClient, {TokenStore? tokens, int installed = 41, DateTime? now, WidgetHost? widgets}) => runBackgroundChecks(
        prefs: prefs,
        tokenStore: tokens ?? _MemoryTokenStore(_tokens()),
        notifier: notifier,
        installedVersionCode: installed,
        widgetHost: widgets ?? _FakeWidgetHost(),
        httpClient: httpClient,
        releaseClient: AppReleaseClient(httpClient: httpClient),
        authClient: CognitoAuthClient(httpClient: httpClient),
        nowUtc: now ?? _wednesdayMorning,
      );

  group('app updates', () {
    test('notifies once when the published versionCode passes the installed one', () async {
      final httpClient = client(publishedVersionCode: 47);

      await run(httpClient, installed: 41);
      await run(httpClient, installed: 41);

      final updates = notifier.shown.where((n) => n.channel == NotificationChannel.appUpdates).toList();
      expect(updates, hasLength(1));
      expect(updates.single.title, 'Update available: v1.1.0');
      expect(updates.single.route, AppRoutes.appUpdates);
    });

    test('stays quiet when the installed build is current', () async {
      await run(client(publishedVersionCode: 41), installed: 41);

      expect(notifier.shown.where((n) => n.channel == NotificationChannel.appUpdates), isEmpty);
    });

    test('notifies again for the next release', () async {
      await run(client(publishedVersionCode: 47));
      await run(client(publishedVersionCode: 48));

      expect(notifier.shown.where((n) => n.channel == NotificationChannel.appUpdates), hasLength(2));
    });

    test('respects the app-updates toggle', () async {
      await prefs.setBool('notify_app_updates', false);

      await run(client(publishedVersionCode: 47));

      expect(notifier.shown.where((n) => n.channel == NotificationChannel.appUpdates), isEmpty);
      expect(requests.where((r) => r.method == 'HEAD'), isEmpty);
    });
  });

  group('weekly model reports', () {
    test('sends a due sport\'s report with the scorecard bearer token, once per slot', () async {
      final httpClient = client(publishedVersionCode: 41, scorecards: {SportIds.nfl: _scorecard(SportIds.nfl)});

      await run(httpClient);
      await run(httpClient);

      final reports = notifier.shown.where((n) => n.channel == NotificationChannel.modelReports).toList();
      expect(reports, hasLength(1));
      expect(reports.single.title, 'NFL · Wk 5 model report');
      expect(reports.single.body, 'Win Probability: 75% (9/12)');
      expect(reports.single.route, AppRoutes.performance(SportIds.nfl));
      final nflRequest = requests.firstWhere((r) => r.url.path == '/nfl/model-performance');
      expect(nflRequest.headers['Authorization'], 'id-token');
      // Only sports whose slot is inside the grace period are fetched.
      expect(requests.where((r) => r.method == 'GET').map((r) => r.url.pathSegments.first).toSet(), {SportIds.nfl, SportIds.f1});
    });

    test('skips a period it already reported (off-season)', () async {
      await prefs.setString(reportLabelKey(SportIds.nfl), 'Wk 5');

      await run(client(publishedVersionCode: 41, scorecards: {SportIds.nfl: _scorecard(SportIds.nfl)}));

      expect(notifier.shown, isEmpty);
      expect(prefs.getString(reportSlotKey(SportIds.nfl)), DateTime.utc(2026, 10, 7, 14).toIso8601String());
    });

    test('respects a sport\'s toggle', () async {
      await prefs.setBool('notify_report_nfl', false);

      await run(client(publishedVersionCode: 41, scorecards: {SportIds.nfl: _scorecard(SportIds.nfl)}));

      expect(notifier.shown, isEmpty);
      expect(requests.where((r) => r.url.path == '/nfl/model-performance'), isEmpty);
    });

    test('does nothing when signed out, leaving the slot due', () async {
      await run(client(publishedVersionCode: 41, scorecards: {SportIds.nfl: _scorecard(SportIds.nfl)}), tokens: _MemoryTokenStore());

      expect(notifier.shown, isEmpty);
      expect(prefs.getString(reportSlotKey(SportIds.nfl)), isNull);
    });

    test('leaves the slot due when the scorecard request fails, so the next run retries', () async {
      await run(client(publishedVersionCode: 41, scorecardStatus: 502));

      expect(notifier.shown, isEmpty);
      expect(prefs.getString(reportSlotKey(SportIds.nfl)), isNull);
    });

    test('refreshes a near-expiry token and stores the new one', () async {
      final tokens = _MemoryTokenStore(_tokens(expiresIn: Duration.zero));

      await run(client(publishedVersionCode: 41, scorecards: {SportIds.nfl: _scorecard(SportIds.nfl)}), tokens: tokens);

      expect(requests.firstWhere((r) => r.url.path == '/nfl/model-performance').headers['Authorization'], 'id-2');
      expect(jsonDecode(tokens.value!)['idToken'], 'id-2');
      expect(jsonDecode(tokens.value!)['refreshToken'], 'refresh');
    });
  });

  group('home-screen widgets', () {
    test("refreshes each placed widget's sport and redraws", () async {
      final host = _FakeWidgetHost([(id: 7, kind: HomeWidgetKind.accuracy)])..sports[7] = SportIds.nfl;

      await run(client(publishedVersionCode: 41, scorecards: {SportIds.nfl: _scorecard(SportIds.nfl)}), widgets: host);

      expect(host.saved[HomeWidgetKind.accuracy.dataKey(SportIds.nfl)]?['season_pct'], 0.68);
      expect(host.redrawn, [HomeWidgetKind.accuracy]);
    });

    test('leaves widgets alone when signed out', () async {
      final host = _FakeWidgetHost([(id: 7, kind: HomeWidgetKind.accuracy)])..sports[7] = SportIds.nfl;

      await run(client(publishedVersionCode: 41), tokens: _MemoryTokenStore(), widgets: host);

      expect(host.saved, isEmpty);
      expect(host.redrawn, isEmpty);
    });
  });
}

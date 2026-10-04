import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import 'package:package_info_plus/package_info_plus.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:workmanager/workmanager.dart';

import '../api/api_routes.dart';
import '../auth/auth_repository.dart' show markSessionRenewed;
import '../auth/cognito_auth_client.dart';
import '../auth/token_store.dart';
import '../config/app_config.dart';
import '../models/model_performance.dart';
import '../routing/app_routes.dart';
import 'app_release.dart';
import 'local_notifications.dart';
import 'model_report.dart';
import 'notification_settings.dart';
import 'widget_sync.dart';

const backgroundChecksTask = 'background-checks';

/// Android's WorkManager may stretch this under Doze or battery saver.
const backgroundChecksInterval = Duration(hours: 1);

const updateNotificationId = 1;
const _reportNotificationIdBase = 100;
const notifiedVersionCodeKey = 'notified_version_code';
String reportSlotKey(String sportId) => 'report_slot_$sportId';
String reportLabelKey(String sportId) => 'report_label_$sportId';

@pragma('vm:entry-point')
void backgroundChecksDispatcher() {
  Workmanager().executeTask((task, inputData) async {
    try {
      final info = await PackageInfo.fromPlatform();
      await runBackgroundChecks(
        prefs: await SharedPreferences.getInstance(),
        tokenStore: SecureTokenStore(),
        notifier: DeviceNotifier(),
        installedVersionCode: int.parse(info.buildNumber),
      );
    } catch (error, stack) {
      debugPrint('[BackgroundChecks] failed: $error\n$stack');
    }
    // Always true: a failed run waits for the next interval instead of
    // WorkManager's retry backoff.
    return true;
  });
}

Future<void> scheduleBackgroundChecks() async {
  await Workmanager().initialize(backgroundChecksDispatcher);
  await Workmanager().registerPeriodicTask(
    backgroundChecksTask,
    backgroundChecksTask,
    frequency: backgroundChecksInterval,
    constraints: Constraints(networkType: NetworkType.connected),
    existingWorkPolicy: ExistingWorkPolicy.keep,
  );
}

/// One pass of the hourly job: an update notification when the published
/// APK's versionCode passes the installed one, each enabled sport's weekly
/// model report once its slot (model_report.dart) arrives, and fresh data
/// for the placed home-screen widgets (widget_sync.dart).
Future<void> runBackgroundChecks({
  required SharedPreferences prefs,
  required TokenStore tokenStore,
  required LocalNotifier notifier,
  required int installedVersionCode,
  WidgetHost widgetHost = const DeviceWidgetHost(),
  AppReleaseClient? releaseClient,
  CognitoAuthClient? authClient,
  http.Client? httpClient,
  DateTime? nowUtc,
}) async {
  // The foreground isolate may have changed settings since this isolate's
  // SharedPreferences cache was filled.
  await prefs.reload();
  final settings = NotificationSettings.read(prefs);
  if (settings.appUpdates) {
    await _checkForUpdate(prefs, notifier, installedVersionCode, releaseClient ?? AppReleaseClient(httpClient: httpClient));
  }
  final api = _BackgroundApi(
    prefs,
    tokenStore,
    authClient ?? CognitoAuthClient(httpClient: httpClient, rotateRefreshTokens: true),
    httpClient ?? http.Client(),
  );
  final now = nowUtc ?? DateTime.now().toUtc();
  await _checkModelReports(prefs, notifier, settings, api, now);
  // Signed out: the widgets keep their last data.
  if (await api.idToken() != null) {
    await refreshWidgets(host: widgetHost, get: api.getJson, prefs: prefs, now: now);
  }
}

Future<void> _checkForUpdate(SharedPreferences prefs, LocalNotifier notifier, int installedVersionCode, AppReleaseClient client) async {
  final latest = await client.fetchLatest();
  if (latest == null || latest.versionCode <= installedVersionCode) return;
  if (prefs.getInt(notifiedVersionCodeKey) == latest.versionCode) return;
  await notifier.show(
    id: updateNotificationId,
    channel: NotificationChannel.appUpdates,
    title: 'Update available: v${latest.versionName}',
    body: latest.notes ?? 'Tap to download and install the new version.',
    route: AppRoutes.appUpdates,
  );
  await prefs.setInt(notifiedVersionCodeKey, latest.versionCode);
}

Future<void> _checkModelReports(SharedPreferences prefs, LocalNotifier notifier, NotificationSettings settings, _BackgroundApi api, DateTime nowUtc) async {
  final sports = reportSports;
  String? idToken;
  for (var i = 0; i < sports.length; i++) {
    final sport = sports[i];
    if (!settings.reportsFor(sport.id)) continue;
    final slot = latestReportSlot(sport.id, nowUtc);
    final lastSlot = DateTime.tryParse(prefs.getString(reportSlotKey(sport.id)) ?? '');
    if (!isReportDue(slot: slot, lastReportedSlot: lastSlot, nowUtc: nowUtc)) continue;

    idToken ??= await api.idToken();
    // Signed out or the 30-day session ended: nothing to report with.
    if (idToken == null) return;

    final ModelPerformance performance;
    try {
      performance = await api.modelPerformance(sport.id, idToken);
    } catch (error) {
      // Left due, so the next run retries.
      debugPrint('[BackgroundChecks] ${sport.id} scorecard failed: $error');
      continue;
    }
    await prefs.setString(reportSlotKey(sport.id), slot.toIso8601String());

    final report = buildModelReport(sport, performance);
    // No graded period, or the same period as last week's report (off-season).
    if (report == null || report.periodLabel == prefs.getString(reportLabelKey(sport.id))) continue;
    await notifier.show(
      id: _reportNotificationIdBase + i,
      channel: NotificationChannel.modelReports,
      title: report.title,
      body: report.body,
      route: AppRoutes.performance(sport.id),
    );
    await prefs.setString(reportLabelKey(sport.id), report.periodLabel);
  }
}

/// The background isolate has no Riverpod/AuthRepository, so it reads the
/// stored tokens and refreshes them directly.
class _BackgroundApi {
  _BackgroundApi(this._prefs, this._tokenStore, this._authClient, this._httpClient);

  static const _timeout = Duration(seconds: 30);

  final SharedPreferences _prefs;
  final TokenStore _tokenStore;
  final CognitoAuthClient _authClient;
  final http.Client _httpClient;

  String? _idToken;

  Future<String?> idToken() async => _idToken ??= await _resolveIdToken();

  Future<String?> _resolveIdToken() async {
    final raw = await _tokenStore.read();
    if (raw == null) return null;
    var tokens = CognitoTokens.fromJson(jsonDecode(raw) as Map<String, dynamic>);
    if (!tokens.isNearExpiry) return tokens.idToken;
    try {
      tokens = await _authClient.refresh(tokens.refreshToken);
    } catch (error) {
      debugPrint('[BackgroundChecks] token refresh failed: $error');
      return null;
    }
    await _tokenStore.write(jsonEncode(tokens.toJson()));
    await markSessionRenewed(_prefs);
    return tokens.idToken;
  }

  Future<ModelPerformance> modelPerformance(String sportId, String idToken) async =>
      ModelPerformance.fromJson(await getJson(ApiRoutes.modelPerformance(sportId)) as Map<String, dynamic>);

  /// 2xx only; predict-read's 202 "computing" body is returned like a 200.
  Future<Object?> getJson(String path, {Map<String, String>? queryParameters}) async {
    final token = await idToken();
    if (token == null) throw StateError('Not signed in');
    final uri = Uri.parse('${AppConfig.apiBaseUrl}$path').replace(queryParameters: queryParameters);
    final response = await _httpClient.get(uri, headers: {'Authorization': token}).timeout(_timeout);
    if (response.statusCode < 200 || response.statusCode >= 300) throw StateError('GET $uri returned ${response.statusCode}');
    return jsonDecode(response.body);
  }
}

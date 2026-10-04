import 'package:flutter_riverpod/legacy.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../models/sport_config.dart';
import 'model_report.dart';

/// Sports with a weekly model report: active, with a scorecard, and a
/// report day in model_report.dart.
List<SportConfig> get reportSports =>
    kSports.where((s) => s.active && s.hasPerformanceTab && reportWeekday.containsKey(s.id)).toList();

String _reportKey(String sportId) => 'notify_report_$sportId';
const _appUpdatesKey = 'notify_app_updates';

/// The user's notification toggles, in SharedPreferences so the
/// background isolate (background_checks.dart) reads the same values.
/// Everything defaults on.
class NotificationSettings {
  const NotificationSettings({required this.reports, required this.appUpdates});

  final Map<String, bool> reports;
  final bool appUpdates;

  bool reportsFor(String sportId) => reports[sportId] ?? true;

  static NotificationSettings read(SharedPreferences prefs) => NotificationSettings(
        reports: {for (final sport in reportSports) sport.id: prefs.getBool(_reportKey(sport.id)) ?? true},
        appUpdates: prefs.getBool(_appUpdatesKey) ?? true,
      );
}

class NotificationSettingsController extends StateNotifier<NotificationSettings?> {
  NotificationSettingsController({SharedPreferences? prefs}) : _prefs = prefs, super(null) {
    _load();
  }

  SharedPreferences? _prefs;

  Future<SharedPreferences> get _prefsInstance async => _prefs ??= await SharedPreferences.getInstance();

  Future<void> _load() async {
    state = NotificationSettings.read(await _prefsInstance);
  }

  Future<void> setReports(String sportId, bool enabled) async {
    await (await _prefsInstance).setBool(_reportKey(sportId), enabled);
    await _load();
  }

  Future<void> setAppUpdates(bool enabled) async {
    await (await _prefsInstance).setBool(_appUpdatesKey, enabled);
    await _load();
  }
}

final notificationSettingsProvider =
    StateNotifierProvider<NotificationSettingsController, NotificationSettings?>((ref) => NotificationSettingsController());

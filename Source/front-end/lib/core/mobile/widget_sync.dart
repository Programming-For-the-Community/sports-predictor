import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:home_widget/home_widget.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../api/api_routes.dart';
import '../models/event.dart';
import '../models/event_status.dart';
import '../models/f1_event.dart';
import '../models/f1_prediction.dart';
import '../models/field_event.dart';
import '../models/field_prediction.dart';
import '../models/model_performance.dart';
import '../models/prediction.dart';
import '../models/sport_config.dart';
import 'model_report.dart' show easternOffsetHours;
import 'widget_data.dart';

/// An authorized GET returning decoded JSON -- ApiClient in the app,
/// background_checks.dart's own client in the WorkManager isolate.
typedef JsonGetter = Future<Object?> Function(String path, {Map<String, String>? queryParameters});

/// The placed widgets and their stored data, via home_widget.
abstract interface class WidgetHost {
  Future<List<({int id, HomeWidgetKind kind})>> placedWidgets();
  Future<String?> sportFor(int widgetId);
  Future<void> setSport(int widgetId, String sportId);
  Future<void> save(String key, Map<String, Object?> data);
  Future<void> redraw(HomeWidgetKind kind);
}

class DeviceWidgetHost implements WidgetHost {
  const DeviceWidgetHost();

  @override
  Future<List<({int id, HomeWidgetKind kind})>> placedWidgets() async {
    final installed = await HomeWidget.getInstalledWidgets();
    return [
      for (final widget in installed)
        if (widget.androidWidgetId != null && HomeWidgetKind.fromClassName(widget.androidClassName) != null)
          (id: widget.androidWidgetId!, kind: HomeWidgetKind.fromClassName(widget.androidClassName)!),
    ];
  }

  @override
  Future<String?> sportFor(int widgetId) => HomeWidget.getWidgetData<String>(widgetSportKey(widgetId));

  @override
  Future<void> setSport(int widgetId, String sportId) async {
    await HomeWidget.saveWidgetData<String>(widgetSportKey(widgetId), sportId);
  }

  @override
  Future<void> save(String key, Map<String, Object?> data) async {
    await HomeWidget.saveWidgetData<String>(key, jsonEncode(data));
  }

  @override
  Future<void> redraw(HomeWidgetKind kind) async {
    await HomeWidget.updateWidget(qualifiedAndroidName: kind.qualifiedAndroidName);
  }
}

/// Accuracy moves once a day (the model-performance job); picks move when
/// predictions are written.
const accuracyRefreshInterval = Duration(hours: 6);
const picksRefreshInterval = Duration(hours: 2);

/// predict-read has no batch route, so a busy day (NCAA FB Saturday) is
/// capped at this many prediction calls.
const maxPredictionRequests = 25;

String _refreshedKey(HomeWidgetKind kind, String sportId) => 'widget_refreshed_${kind.name}_$sportId';

/// Refreshes the data behind every placed widget whose copy is older than
/// its interval (or all of them with [force]), then redraws.
Future<void> refreshWidgets({
  required WidgetHost host,
  required JsonGetter get,
  required SharedPreferences prefs,
  required DateTime now,
  bool force = false,
}) async {
  final wanted = await _placedSports(host);
  for (final MapEntry(key: kind, value: sportIds) in wanted.entries) {
    for (final sportId in sportIds) {
      if (force || _isStale(prefs, kind, sportId, now)) await _refreshOne(host, get, prefs, kind, sportId, now);
    }
    try {
      await host.redraw(kind);
    } catch (error) {
      debugPrint('[Widgets] ${kind.name} redraw failed: $error');
    }
  }
}

/// The sports each widget kind is currently showing, across every placed
/// widget that has had its sport chosen.
Future<Map<HomeWidgetKind, Set<String>>> _placedSports(WidgetHost host) async {
  final wanted = <HomeWidgetKind, Set<String>>{};
  for (final widget in await host.placedWidgets()) {
    final sportId = await host.sportFor(widget.id);
    if (sportId != null) wanted.putIfAbsent(widget.kind, () => {}).add(sportId);
  }
  return wanted;
}

bool _isStale(SharedPreferences prefs, HomeWidgetKind kind, String sportId, DateTime now) {
  final interval = kind == HomeWidgetKind.accuracy ? accuracyRefreshInterval : picksRefreshInterval;
  final last = DateTime.tryParse(prefs.getString(_refreshedKey(kind, sportId)) ?? '');
  return last == null || now.difference(last) >= interval;
}

/// A failed refresh keeps the widget's last data and is retried next run.
Future<void> _refreshOne(WidgetHost host, JsonGetter get, SharedPreferences prefs, HomeWidgetKind kind, String sportId, DateTime now) async {
  try {
    await host.save(kind.dataKey(sportId), await fetchWidgetData(kind, sportById(sportId), get, now: now));
    await prefs.setString(_refreshedKey(kind, sportId), now.toIso8601String());
  } catch (error) {
    debugPrint('[Widgets] ${kind.name} $sportId refresh failed: $error');
  }
}

/// One widget's data. A sport with nothing to show gets an `empty` message
/// the widget displays instead.
Future<Map<String, Object?>> fetchWidgetData(HomeWidgetKind kind, SportConfig sport, JsonGetter get, {required DateTime now}) async {
  final data = switch (kind) {
    HomeWidgetKind.accuracy => buildAccuracyData(
        sport,
        ModelPerformance.fromJson(await get(ApiRoutes.modelPerformance(sport.id)) as Map<String, dynamic>),
        now: now,
      ),
    HomeWidgetKind.topPicks => await _fetchPicks(sport, get, now),
  };
  return data ??
      {
        'sport': sport.displayName,
        'empty': kind == HomeWidgetKind.accuracy ? 'No graded games yet this season' : 'Nothing scheduled',
        'route': kind.routeFor(sport.id),
        'updated': now.toIso8601String(),
      };
}

Future<Map<String, Object?>?> _fetchPicks(SportConfig sport, JsonGetter get, DateTime now) async {
  final listing = await get(ApiRoutes.events(sport.id), queryParameters: {'status': EventStatus.scheduled}) as Map<String, dynamic>;
  final eventsJson = (listing['events'] as List<dynamic>? ?? []).cast<Map<String, dynamic>>();
  if (sport.id == SportIds.f1) return _f1Picks(sport, eventsJson, get, now);
  if (sport.eventShape == EventShape.field) return _pgaPicks(sport, eventsJson, get, now);
  return _headToHeadPicks(sport, eventsJson, get, now);
}

/// One event's prediction, or null while predict-read is still computing
/// it (its 202 response).
Future<Map<String, dynamic>?> _prediction(SportConfig sport, String eventId, JsonGetter get) async {
  final json = await get(ApiRoutes.eventPrediction(sport.id, eventId)) as Map<String, dynamic>;
  return json['status'] == 'computing' ? null : json;
}

Future<Map<String, Object?>?> _f1Picks(SportConfig sport, List<Map<String, dynamic>> eventsJson, JsonGetter get, DateTime now) async {
  final race = _earliest(eventsJson.map(F1Event.fromJson).where((e) => e.eventType == F1EventType.field), (e) => e.eventDate);
  if (race == null) return null;
  final json = await _prediction(sport, race.eventId, get);
  return json == null ? null : buildF1Picks(sport, F1EventPrediction.fromJson(json), now: now);
}

Future<Map<String, Object?>?> _pgaPicks(SportConfig sport, List<Map<String, dynamic>> eventsJson, JsonGetter get, DateTime now) async {
  final tournament = _earliest(eventsJson.map(FieldEvent.fromJson).where((e) => e.eventType == PgaEventType.field), (e) => e.eventDate);
  if (tournament == null) return null;
  final json = await _prediction(sport, tournament.eventId, get);
  return json == null ? null : buildPgaPicks(sport, FieldEventPrediction.fromJson(json), now: now);
}

Future<Map<String, Object?>?> _headToHeadPicks(SportConfig sport, List<Map<String, dynamic>> eventsJson, JsonGetter get, DateTime now) async {
  final eastern = now.toUtc().subtract(Duration(hours: easternOffsetHours(now.toUtc())));
  final today = eastern.toIso8601String().substring(0, 10);
  final events = eventsJson.map(SportEvent.fromJson).toList();
  final day = nextGameDay(events.map((e) => e.eventDate), today);
  if (day == null) return null;
  final dayEvents = events.where((e) => e.eventDate == day).take(maxPredictionRequests).toList();
  final predictions = <String, EventPrediction>{};
  for (final event in dayEvents) {
    final prediction = await _predictionOrNull(sport, event.eventId, get);
    if (prediction != null) predictions[event.eventId] = prediction;
  }
  return buildHeadToHeadPicks(sport, day, dayEvents, predictions, todayEastern: today, now: now);
}

/// One game's failed prediction is skipped rather than failing the whole day.
Future<EventPrediction?> _predictionOrNull(SportConfig sport, String eventId, JsonGetter get) async {
  try {
    final json = await _prediction(sport, eventId, get);
    return json == null ? null : EventPrediction.fromJson(json);
  } catch (error) {
    debugPrint('[Widgets] ${sport.id} $eventId prediction failed: $error');
    return null;
  }
}

/// A field event that started before today is still the current one until
/// it completes, so "earliest scheduled" is right without a date floor.
T? _earliest<T>(Iterable<T> events, String Function(T) date) {
  final sorted = events.toList()..sort((a, b) => date(a).compareTo(date(b)));
  return sorted.firstOrNull;
}

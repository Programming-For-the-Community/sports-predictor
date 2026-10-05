import '../../static/model_display.dart';
import '../models/event.dart';
import '../models/f1_prediction.dart';
import '../models/field_prediction.dart';
import '../models/model_performance.dart';
import '../models/prediction.dart';
import '../models/sport_config.dart';
import '../routing/app_routes.dart';

/// The two home-screen widgets. [androidName] is the Kotlin
/// AppWidgetProvider class (android/app/src/main/kotlin/.../widgets/).
enum HomeWidgetKind {
  accuracy('ModelAccuracyWidget', 'Model accuracy'),
  topPicks('TopPicksWidget', 'Today\'s top picks');

  const HomeWidgetKind(this.androidName, this.title);

  final String androidName;
  final String title;

  /// home_widget otherwise looks for the class directly under the app's
  /// package, but the providers live in its `widgets` subpackage.
  String get qualifiedAndroidName => 'com.professorchaos0802.sportspredictor.widgets.$androidName';

  /// Where a tap on the widget opens.
  String routeFor(String sportId) => switch (this) {
        HomeWidgetKind.accuracy => AppRoutes.performance(sportId),
        HomeWidgetKind.topPicks => AppRoutes.events(sportId),
      };

  String dataKey(String sportId) => switch (this) {
        HomeWidgetKind.accuracy => 'accuracy_$sportId',
        HomeWidgetKind.topPicks => 'picks_$sportId',
      };

  static HomeWidgetKind? fromClassName(String? className) {
    for (final kind in values) {
      if (className != null && className.endsWith(kind.androidName)) return kind;
    }
    return null;
  }
}

/// The sport a placed widget shows, chosen on the widget setup screen.
String widgetSportKey(int widgetId) => 'widget_sport_$widgetId';

/// Widget taps arrive as `sportspredictor://open?route=/nfl/events`
/// (the widgets' Kotlin providers build them); null for anything else.
String? routeFromWidgetUri(Uri? uri) {
  if (uri == null || uri.scheme != 'sportspredictor' || uri.host != 'open') return null;
  final route = uri.queryParameters['route'];
  return (route != null && route.startsWith('/')) ? route : null;
}

/// The win-probability model's accuracy -- or, for a sport without one
/// (PGA), its first graded yes/no model.
Map<String, Object?>? buildAccuracyData(SportConfig sport, ModelPerformance performance, {required DateTime now}) {
  final graded = performance.models.where((m) => !m.isAmount && m.season.n > 0 && m.season.value != null).toList();
  if (graded.isEmpty) return null;
  final record = graded.firstWhere((m) => m.modelName == 'win-probability', orElse: () => graded.first);
  final last = record.lastPeriod;
  final trend = record.history.where((w) => w.value != null).map((w) => w.value!).toList();
  return {
    'sport': sport.displayName,
    'model': modelDisplayName(record.modelName),
    'season_pct': record.season.value,
    'season_n': record.season.n,
    'last_label': last?.label,
    'last_pct': last?.value,
    'last_n': last?.n,
    'trend': trend.length <= 6 ? trend : trend.sublist(trend.length - 6),
    'route': HomeWidgetKind.accuracy.routeFor(sport.id),
    'updated': now.toIso8601String(),
  };
}

const maxPicks = 3;

/// The day the head-to-head picks cover: today when anything is scheduled
/// today, otherwise the next day with games. Event dates are Eastern
/// calendar dates (YYYY-MM-DD).
String? nextGameDay(Iterable<String> eventDates, String todayEastern) {
  final upcoming = eventDates.where((d) => d.compareTo(todayEastern) >= 0).toList()..sort();
  return upcoming.isEmpty ? null : upcoming.first;
}

/// The day's most confident winner picks. [predictions] is keyed by event id.
Map<String, Object?>? buildHeadToHeadPicks(
  SportConfig sport,
  String day,
  List<SportEvent> events,
  Map<String, EventPrediction> predictions, {
  required String todayEastern,
  required DateTime now,
}) {
  final rows = <({String label, String value, double pct})>[];
  for (final event in events) {
    final prediction = predictions[event.eventId];
    final home = event.participants.where((p) => p.role == 'home').firstOrNull;
    final away = event.participants.where((p) => p.role == 'away').firstOrNull;
    if (prediction == null || home == null || away == null) continue;
    final homeName = home.abbreviation ?? home.entityId;
    final awayName = away.abbreviation ?? away.entityId;
    final homeFavored = prediction.homeWinProbability >= 0.5;
    final pct = homeFavored ? prediction.homeWinProbability : 1 - prediction.homeWinProbability;
    rows.add((label: '$awayName @ $homeName', value: '${homeFavored ? homeName : awayName} ${_pct(pct)}', pct: pct));
  }
  if (rows.isEmpty) return null;
  rows.sort((a, b) => b.pct.compareTo(a.pct));
  return _picks(sport, day == todayEastern ? 'Today' : _dayLabel(day), rows, now);
}

/// A PGA tournament's most likely top-10 finishers.
Map<String, Object?>? buildPgaPicks(SportConfig sport, FieldEventPrediction prediction, {required DateTime now}) {
  final rows = [
    for (final golfer in prediction.field)
      if (golfer.top10Probability != null)
        (label: golfer.name ?? golfer.entityId, value: 'Top 10 · ${_pct(golfer.top10Probability!.value)}', pct: golfer.top10Probability!.value),
  ]..sort((a, b) => b.pct.compareTo(a.pct));
  if (rows.isEmpty) return null;
  return _picks(sport, prediction.tournamentName ?? 'This week', rows, now);
}

/// An F1 race's most likely winners.
Map<String, Object?>? buildF1Picks(SportConfig sport, F1EventPrediction prediction, {required DateTime now}) {
  final rows = [
    for (final driver in prediction.field)
      if (driver.winProbability != null)
        (label: driver.name ?? driver.entityId, value: 'Win · ${_pct(driver.winProbability!.value)}', pct: driver.winProbability!.value),
  ]..sort((a, b) => b.pct.compareTo(a.pct));
  if (rows.isEmpty) return null;
  return _picks(sport, prediction.raceName ?? 'Next race', rows, now);
}

Map<String, Object?> _picks(SportConfig sport, String heading, List<({String label, String value, double pct})> rows, DateTime now) => {
      'sport': sport.displayName,
      'heading': heading,
      'picks': [
        for (final row in rows.take(maxPicks)) {'label': row.label, 'value': row.value, 'pct': row.pct},
      ],
      'route': HomeWidgetKind.topPicks.routeFor(sport.id),
      'updated': now.toIso8601String(),
    };

String _pct(double value) => '${(value * 100).round()}%';

const _weekdays = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const _months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/// "2026-10-11" -> "Sun Oct 11"
String _dayLabel(String isoDate) {
  final date = DateTime.parse(isoDate);
  return '${_weekdays[date.weekday - 1]} ${_months[date.month - 1]} ${date.day}';
}

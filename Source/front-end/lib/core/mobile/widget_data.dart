import '../../static/model_display.dart';
import '../../static/prop_benchmarks.dart';
import '../models/event.dart';
import '../models/event_leaders.dart';
import '../models/f1_prediction.dart';
import '../models/field_prediction.dart';
import '../models/model_performance.dart';
import '../models/prediction.dart';
import '../models/sport_config.dart';
import '../routing/app_routes.dart';

/// The home-screen widgets. [androidName] is the Kotlin AppWidgetProvider
/// class (android/app/src/main/kotlin/.../widgets/).
enum HomeWidgetKind {
  accuracy('ModelAccuracyWidget', 'Model accuracy'),
  accuracyWide('ModelAccuracyWideWidget', 'Model accuracy'),
  topPicks('TopPicksWidget', 'Today\'s top picks'),
  topProps('TopPropsWidget', 'Top player props'),
  picksAndProps('PicksAndPropsWidget', 'Top picks and player props');

  const HomeWidgetKind(this.androidName, this.title);

  final String androidName;
  final String title;

  /// home_widget otherwise looks for the class directly under the app's
  /// package, but the providers live in its `widgets` subpackage.
  String get qualifiedAndroidName => 'com.professorchaos0802.sportspredictor.widgets.$androidName';

  bool get showsAccuracy => this == HomeWidgetKind.accuracy || this == HomeWidgetKind.accuracyWide;

  /// Lists player props, so it is offered only for a sport in propStatsBySport.
  bool get showsProps => this == HomeWidgetKind.topProps || this == HomeWidgetKind.picksAndProps;

  /// Where a tap on the widget opens.
  String routeFor(String sportId) => showsAccuracy ? AppRoutes.performance(sportId) : AppRoutes.events(sportId);

  /// Widgets of the same family draw from one stored copy of a sport's data.
  String dataKey(String sportId) => showsAccuracy ? 'accuracy_$sportId' : 'picks_$sportId';

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

const _scoreModels = ['score-margin', 'home-score', 'away-score'];

/// The win-probability model's accuracy -- or, for a sport without one
/// (PGA), its first graded yes/no model -- with its confidence bands and the
/// score models' average misses.
Map<String, Object?>? buildAccuracyData(SportConfig sport, ModelPerformance performance, {required DateTime now}) {
  final graded = performance.models.where((m) => !m.isAmount && m.season.n > 0 && m.season.value != null).toList();
  if (graded.isEmpty) return null;
  final record = graded.firstWhere((m) => m.modelName == 'win-probability', orElse: () => graded.first);
  final last = record.lastPeriod;
  return {
    'sport': sport.displayName,
    'model': modelDisplayName(record.modelName),
    'season_pct': record.season.value,
    'season_n': record.season.n,
    'last_label': last?.label,
    'last_pct': last?.value,
    'bands': [
      if (record.bandKind == ModelPerformanceRecord.bandKindConfidence)
        for (final band in record.bands) {'tag': band.tag, 'pct': band.pct},
    ],
    'misses': [
      for (final model in performance.models)
        if (_scoreModels.contains(model.modelName) && model.season.value != null) _missRow(model),
    ],
    'route': HomeWidgetKind.accuracy.routeFor(sport.id),
    'updated': now.toIso8601String(),
  };
}

Map<String, Object?> _missRow(ModelPerformanceRecord model) {
  final display = modelDisplay(model.modelName);
  return {
    'label': modelDisplayName(model.modelName),
    'value': '±${model.season.value!.toStringAsFixed(display.missDecimals)}',
    'unit': display.valueUnit,
  };
}

/// The most picks stored for a sport -- every pick row the picks widgets' layouts hold; a
/// widget shows as many as its height has room for.
const maxPicks = 10;

/// The day the head-to-head picks cover: today when anything is scheduled
/// today, otherwise the next day with games. Event dates are Eastern
/// calendar dates (YYYY-MM-DD).
String? nextGameDay(Iterable<String> eventDates, String todayEastern) {
  final upcoming = eventDates.where((d) => d.compareTo(todayEastern) >= 0).toList()..sort();
  return upcoming.isEmpty ? null : upcoming.first;
}

/// Roughly how long after its start each sport reaches halftime (hockey: the
/// middle of the second period).
const halftimeAfterStart = <String, Duration>{
  SportIds.nfl: Duration(minutes: 90),
  SportIds.ncaafb: Duration(minutes: 100),
  SportIds.nba: Duration(minutes: 65),
  SportIds.ncaambb: Duration(minutes: 50),
  SportIds.nhl: Duration(minutes: 75),
};

/// The [limit] games a day's picks are drawn from: those not yet at halftime
/// first, earliest start first, then games past it. On a day with more games
/// than [limit], each game reaching halftime makes room for the next to
/// start. A game without a kickoff time goes last.
List<SportEvent> gamesToPredict(SportConfig sport, Iterable<SportEvent> dayEvents, DateTime now, {required int limit}) {
  final untilHalftime = halftimeAfterStart[sport.id] ?? Duration.zero;
  final ordered = [
    for (final event in dayEvents) (event: event, kickoff: DateTime.tryParse(event.kickoffTime ?? '')),
  ];
  int group(DateTime? kickoff) {
    if (kickoff == null) return 2;
    return kickoff.add(untilHalftime).isAfter(now) ? 0 : 1;
  }

  ordered.sort((a, b) {
    final byGroup = group(a.kickoff).compareTo(group(b.kickoff));
    if (byGroup != 0) return byGroup;
    final byKickoff = a.kickoff == null ? 0 : a.kickoff!.compareTo(b.kickoff!);
    return byKickoff != 0 ? byKickoff : a.event.eventId.compareTo(b.event.eventId);
  });
  return [for (final entry in ordered.take(limit)) entry.event];
}

/// The day's most confident winner picks. [predictions] is keyed by event id.
/// [props] (buildTopProps) is stored alongside for the player-props widgets.
Map<String, Object?>? buildHeadToHeadPicks(
  SportConfig sport,
  String day,
  List<SportEvent> events,
  Map<String, EventPrediction> predictions, {
  required String todayEastern,
  required DateTime now,
  List<Map<String, Object?>>? props,
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
  return {
    ..._picks(sport, day == todayEastern ? 'Today' : _dayLabel(day), rows, now),
    if (props != null) 'props': props,
  };
}

/// The day's player projections the model has been closest on. Each one's
/// tolerance is that player's own average miss on the stat this season
/// ([performance]'s entityMisses), and rows run from the smallest miss as a
/// share of the projection. A projection below propFloorShare of its stat's
/// big game, or for a player without a graded history, is left out.
List<Map<String, Object?>> buildTopProps(
  SportConfig sport,
  List<SportEvent> events,
  Map<String, EventPrediction> predictions,
  ModelPerformance performance,
) {
  final stats = propStatsBySport[sport.id];
  if (stats == null) return const [];
  final missesByModel = {for (final model in performance.models) model.modelName: model.entityMisses};

  final candidates = <_PropCandidate>[];
  for (final event in events) {
    final leaders = predictions[event.eventId]?.leaders;
    if (leaders == null) continue;
    for (final (role, team) in [('away', leaders.away), ('home', leaders.home)]) {
      final abbreviation = event.participants.where((p) => p.role == role).firstOrNull?.abbreviation;
      for (final player in team.categories.values.expand((players) => players)) {
        candidates.addAll(_playerCandidates(sport, event, player, abbreviation, stats, missesByModel));
      }
    }
  }
  candidates.sort((a, b) => a.share.compareTo(b.share));

  final rows = <Map<String, Object?>>[];
  final players = <String>{};
  final perStat = <String, int>{};
  for (final candidate in candidates) {
    if (rows.length == maxProps) break;
    if (players.contains(candidate.entityId) || (perStat[candidate.statKey] ?? 0) >= maxPropsPerStat) continue;
    players.add(candidate.entityId);
    perStat[candidate.statKey] = (perStat[candidate.statKey] ?? 0) + 1;
    rows.add(candidate.row);
  }
  return rows;
}

typedef _PropCandidate = ({String entityId, String statKey, double share, Map<String, Object?> row});

/// One candidate per listed stat the player has both a big enough projection
/// and a graded history for.
List<_PropCandidate> _playerCandidates(
  SportConfig sport,
  SportEvent event,
  PlayerStatLine player,
  String? abbreviation,
  Map<String, PropStat> stats,
  Map<String, Map<String, PerformanceWindow>> missesByModel,
) {
  final candidates = <_PropCandidate>[];
  for (final MapEntry(key: statKey, value: stat) in stats.entries) {
    final projection = player.stats[statKey];
    final miss = missesByModel[propModelName(statKey)]?[player.entityId]?.value;
    if (projection == null || miss == null || projection < stat.bigGame * propFloorShare) continue;
    candidates.add((
      entityId: player.entityId,
      statKey: statKey,
      share: miss / projection,
      row: _propRow(sport, event, player, abbreviation, stat, projection, miss),
    ));
  }
  return candidates;
}

Map<String, Object?> _propRow(
  SportConfig sport,
  SportEvent event,
  PlayerStatLine player,
  String? abbreviation,
  PropStat stat,
  double projection,
  double miss,
) =>
    {
      'name': _shortName(player.displayName),
      'team': abbreviation,
      'value': projection.toStringAsFixed(stat.decimals),
      'unit': stat.unit,
      'tolerance': '±${miss.toStringAsFixed(stat.decimals)}',
      'route': AppRoutes.eventDetail(sport.id, event.eventId),
    };

/// "Patrick Mahomes" -> "P. Mahomes"; a one-word name is left as it is.
String _shortName(String name) {
  final space = name.indexOf(' ');
  return space <= 0 ? name : '${name[0]}. ${name.substring(space + 1)}';
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

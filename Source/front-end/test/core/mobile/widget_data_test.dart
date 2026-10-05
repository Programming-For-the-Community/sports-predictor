import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/mobile/widget_data.dart';
import 'package:front_end/core/mobile/widget_sync.dart';
import 'package:front_end/core/models/event.dart';
import 'package:front_end/core/models/model_performance.dart';
import 'package:front_end/core/models/prediction.dart';
import 'package:front_end/core/models/sport_config.dart';
import 'package:front_end/core/routing/app_routes.dart';

import '../../support/model_performance_fixtures.dart';

final _now = DateTime.utc(2026, 10, 4, 15); // 11 AM Eastern, Sunday Oct 4

Map<String, dynamic> _game(String id, String date, String away, String home, {String? kickoff}) => {
      'event_id': id,
      'event_date': date,
      'kickoff_time': kickoff,
      'status': 'scheduled',
      'participants': [
        {'entity_id': '$id-a', 'role': 'away', 'abbreviation': away},
        {'entity_id': '$id-h', 'role': 'home', 'abbreviation': home},
      ],
    };

Map<String, dynamic> _prediction(double homeWin) => {
      'predictions': {
        'win_probability': {'home_win_probability': homeWin, 'model_version': 9},
        'margin': {'value': 3.0},
        'home_score': {'value': 24.0},
        'away_score': {'value': 21.0},
      },
    };

/// A prediction whose `leaders` block lists [away] and [home] players, each
/// (entity id, name, category, stats).
Map<String, dynamic> _predictionWithLeaders({
  List<(String, String, String, Map<String, double>)> away = const [],
  List<(String, String, String, Map<String, double>)> home = const [],
}) {
  Map<String, dynamic> team(List<(String, String, String, Map<String, double>)> players) => {
        for (final category in {for (final player in players) player.$3})
          category: [
            for (final (id, name, playerCategory, stats) in players)
              if (playerCategory == category) {'entity_id': id, 'name': name, ...stats},
          ],
      };
  return {..._prediction(0.6), 'leaders': {'away': team(away), 'home': team(home)}};
}

/// A scorecard whose prop models carry each player's own average miss:
/// {stat slug: {entity id: miss}}.
Map<String, dynamic> _scorecardWithMisses(Map<String, Map<String, double>> misses) => {
      'sport': 'nfl',
      'season': 2026,
      'period_kind': 'week',
      'models': [
        for (final MapEntry(key: stat, value: players) in misses.entries)
          {
            'model_name': 'player-prop-$stat',
            'version': 3,
            'kind': 'amount',
            'band_kind': 'predicted_amount',
            'season': {'value': 40.0, 'n': 60},
            'entity_misses': {
              for (final MapEntry(key: id, value: miss) in players.entries) id: {'value': miss, 'n': 4},
            },
          },
      ],
    };

List<Map<String, Object?>> _props(
  Map<String, dynamic> prediction,
  Map<String, Map<String, double>> misses, {
  String sport = SportIds.nfl,
}) =>
    buildTopProps(
      sportById(sport),
      [SportEvent.fromJson(_game('g1', '2026-10-04', 'KC', 'JAX'))],
      {'g1': EventPrediction.fromJson(prediction)},
      ModelPerformance.fromJson(_scorecardWithMisses(misses)),
    );

/// Answers GETs from a path -> JSON map, recording every path asked for.
JsonGetter _api(Map<String, Object?> responses, List<String> seen) => (path, {queryParameters}) async {
      seen.add(path);
      if (!responses.containsKey(path)) throw StateError('unexpected GET $path');
      return responses[path];
    };

void main() {
  group('routeFromWidgetUri', () {
    test('reads the route from a widget tap', () {
      expect(routeFromWidgetUri(Uri.parse('sportspredictor://open?route=/nfl/events')), '/nfl/events');
    });

    test('ignores anything else', () {
      expect(routeFromWidgetUri(null), isNull);
      expect(routeFromWidgetUri(Uri.parse('https://example.com/?route=/nfl/events')), isNull);
      expect(routeFromWidgetUri(Uri.parse('sportspredictor://open')), isNull);
      expect(routeFromWidgetUri(Uri.parse('sportspredictor://open?route=nfl')), isNull);
    });
  });

  test('HomeWidgetKind.fromClassName matches the Kotlin provider class', () {
    expect(HomeWidgetKind.fromClassName('.widgets.TopPicksWidget'), HomeWidgetKind.topPicks);
    expect(HomeWidgetKind.fromClassName('com.x.widgets.ModelAccuracyWidget'), HomeWidgetKind.accuracy);
    expect(HomeWidgetKind.fromClassName('.widgets.ModelAccuracyWideWidget'), HomeWidgetKind.accuracyWide);
    expect(HomeWidgetKind.fromClassName('.widgets.TopPropsWidget'), HomeWidgetKind.topProps);
    expect(HomeWidgetKind.fromClassName('.widgets.PicksAndPropsWidget'), HomeWidgetKind.picksAndProps);
    expect(HomeWidgetKind.fromClassName('.Other'), isNull);
  });

  test('widgets of one family share a stored copy', () {
    expect(HomeWidgetKind.accuracyWide.dataKey('nfl'), HomeWidgetKind.accuracy.dataKey('nfl'));
    expect(HomeWidgetKind.topProps.dataKey('nfl'), HomeWidgetKind.topPicks.dataKey('nfl'));
    expect(HomeWidgetKind.picksAndProps.dataKey('nfl'), HomeWidgetKind.topPicks.dataKey('nfl'));
    expect(HomeWidgetKind.accuracy.dataKey('nfl'), isNot(HomeWidgetKind.topPicks.dataKey('nfl')));
  });

  group('buildAccuracyData', () {
    final nfl = sportById(SportIds.nfl);

    test('uses the win-probability model', () {
      final data = buildAccuracyData(
        nfl,
        ModelPerformance(sport: 'nfl', season: 2026, periodKind: 'week', models: [pickRecord(modelName: 'other-pick', season: 0.5), pickRecord()]),
        now: _now,
      )!;

      expect(data['model'], 'Win Probability');
      expect(data['season_pct'], 0.684);
      expect(data['last_label'], 'Wk 3');
      expect(data['last_pct'], 0.75);
      expect(data['route'], AppRoutes.performance(SportIds.nfl));
    });

    test('lists the confidence bands, an early one without a percentage', () {
      final record = pickRecord(bands: [band('HIGH', lo: 0.13, n: 9, pct: 0.89), band('MED', lo: 0.06, hi: 0.13, n: 3, early: true)]);
      final data = buildAccuracyData(nfl, ModelPerformance(sport: 'nfl', season: 2026, periodKind: 'week', models: [record]), now: _now)!;

      expect(data['bands'], [
        {'tag': 'HIGH', 'pct': 0.89},
        {'tag': 'MED', 'pct': null},
      ]);
    });

    test('lists the score models\' average misses', () {
      final data = buildAccuracyData(
        nfl,
        ModelPerformance(sport: 'nfl', season: 2026, periodKind: 'week', models: [
          pickRecord(),
          amountRecord(),
          amountRecord(modelName: 'home-score', season: 7.44),
          amountRecord(modelName: 'player-prop-passing-yards', season: 58.2),
        ]),
        now: _now,
      )!;

      expect(data['misses'], [
        {'label': 'Score Margin', 'value': '±10.2', 'unit': 'pts'},
        {'label': 'Home Score', 'value': '±7.4', 'unit': 'pts'},
      ]);
    });

    test('a chance model has no confidence bands', () {
      final data = buildAccuracyData(
        sportById(SportIds.pga),
        ModelPerformance(sport: 'pga', season: 2026, periodKind: 'event', models: [chanceRecord()]),
        now: _now,
      )!;

      expect(data['bands'], isEmpty);
      expect(data['misses'], isEmpty);
    });

    test('falls back to the first graded yes/no model, never an amount model', () {
      final pga = sportById(SportIds.pga);
      final data = buildAccuracyData(
        pga,
        ModelPerformance(sport: 'pga', season: 2026, periodKind: 'event', models: [amountRecord(), pickRecord(modelName: 'top-10-probability')]),
        now: _now,
      )!;

      expect(data['model'], 'Top 10 Probability');
    });

    test('is null with nothing graded', () {
      expect(
        buildAccuracyData(nfl, ModelPerformance(sport: 'nfl', season: 2026, periodKind: 'week', models: [amountRecord()]), now: _now),
        isNull,
      );
    });
  });

  test('nextGameDay is today when anything is scheduled today, else the next date', () {
    expect(nextGameDay(['2026-10-05', '2026-10-04'], '2026-10-04'), '2026-10-04');
    expect(nextGameDay(['2026-10-11', '2026-10-08', '2026-10-01'], '2026-10-04'), '2026-10-08');
    expect(nextGameDay(['2026-10-01'], '2026-10-04'), isNull);
  });

  group('gamesToPredict', () {
    // _now is 15:00 UTC, and an NFL game reaches halftime 90 minutes in.
    List<String> ids(List<Map<String, dynamic>> games, {required int limit}) =>
        gamesToPredict(sportById(SportIds.nfl), games.map(SportEvent.fromJson), _now, limit: limit).map((e) => e.eventId).toList();

    test('takes the earliest games', () {
      final games = [
        _game('night', '2026-10-04', 'A', 'B', kickoff: '2026-10-04T23:30Z'),
        _game('noon', '2026-10-04', 'C', 'D', kickoff: '2026-10-04T16:00Z'),
        _game('afternoon', '2026-10-04', 'E', 'F', kickoff: '2026-10-04T19:30Z'),
      ];

      expect(ids(games, limit: 2), ['noon', 'afternoon']);
    });

    test('a game in its first half keeps its place', () {
      final games = [
        _game('first-half', '2026-10-04', 'A', 'B', kickoff: '2026-10-04T14:00Z'),
        _game('noon', '2026-10-04', 'C', 'D', kickoff: '2026-10-04T16:00Z'),
        _game('night', '2026-10-04', 'E', 'F', kickoff: '2026-10-04T23:30Z'),
      ];

      expect(ids(games, limit: 2), ['first-half', 'noon']);
    });

    test('a game at halftime gives its place to one yet to reach it', () {
      final games = [
        _game('halftime', '2026-10-04', 'A', 'B', kickoff: '2026-10-04T13:30Z'),
        _game('noon', '2026-10-04', 'C', 'D', kickoff: '2026-10-04T16:00Z'),
        _game('night', '2026-10-04', 'E', 'F', kickoff: '2026-10-04T23:30Z'),
      ];

      expect(ids(games, limit: 2), ['noon', 'night']);
    });

    test('with room for every game, the ones past halftime stay', () {
      final games = [
        _game('second-half', '2026-10-04', 'A', 'B', kickoff: '2026-10-04T13:00Z'),
        _game('noon', '2026-10-04', 'C', 'D', kickoff: '2026-10-04T16:00Z'),
      ];

      expect(ids(games, limit: 25), ['noon', 'second-half']);
    });

    test('a game without a kickoff time goes last', () {
      final games = [
        _game('unknown', '2026-10-04', 'A', 'B'),
        _game('second-half', '2026-10-04', 'C', 'D', kickoff: '2026-10-04T13:00Z'),
        _game('noon', '2026-10-04', 'E', 'F', kickoff: '2026-10-04T16:00Z'),
      ];

      expect(ids(games, limit: 3), ['noon', 'second-half', 'unknown']);
    });
  });

  group('buildTopProps', () {
    test('orders by the player\'s own miss as a share of the projection', () {
      final rows = _props(
        _predictionWithLeaders(
          away: [('qb', 'Patrick Mahomes', 'passing', {'passing_yards': 284.4, 'passing_touchdowns': 1.2})],
          home: [('rb', 'Travis Etienne Jr.', 'rushing', {'rushing_yards': 104})],
        ),
        {
          'passing-yards': {'qb': 38.2},
          'rushing-yards': {'rb': 12.0},
        },
      );

      expect(rows, [
        {'name': 'T. Etienne Jr.', 'team': 'JAX', 'value': '104', 'unit': 'rush yds', 'tolerance': '±12', 'route': '/nfl/events/g1'},
        {'name': 'P. Mahomes', 'team': 'KC', 'value': '284', 'unit': 'pass yds', 'tolerance': '±38', 'route': '/nfl/events/g1'},
      ]);
    });

    test('leaves out a projection below 60% of its big game', () {
      final rows = _props(
        _predictionWithLeaders(away: [
          ('at-floor', 'At Floor', 'rushing', {'rushing_yards': 60}),
          ('below', 'Below Floor', 'rushing', {'rushing_yards': 59.9}),
        ]),
        {
          'rushing-yards': {'at-floor': 20.0, 'below': 1.0},
        },
      );

      expect(rows.map((r) => r['name']), ['A. Floor']);
    });

    test('leaves out a player with no miss of their own', () {
      final rows = _props(
        _predictionWithLeaders(away: [('rookie', 'New Rookie', 'rushing', {'rushing_yards': 110})]),
        {
          'rushing-yards': {'someone-else': 5.0},
        },
      );

      expect(rows, isEmpty);
    });

    test('keeps one row per player, their closest stat', () {
      final rows = _props(
        _predictionWithLeaders(away: [('qb', 'Josh Allen', 'passing', {'passing_yards': 260, 'passing_touchdowns': 2.2})]),
        {
          'passing-yards': {'qb': 52.0},
          'passing-touchdowns': {'qb': 0.22},
        },
      );

      expect(rows.single, containsPair('unit', 'pass TDs'));
      expect(rows.single, containsPair('value', '2.2'));
      expect(rows.single, containsPair('tolerance', '±0.2'));
    });

    test('keeps at most two rows per stat', () {
      final rows = _props(
        _predictionWithLeaders(away: [
          for (final i in [1, 2, 3]) ('wr$i', 'Wide Receiver$i', 'receiving', {'receiving_yards': 90}),
        ]),
        {
          'receiving-yards': {'wr1': 10.0, 'wr2': 11.0, 'wr3': 12.0},
        },
      );

      expect(rows.map((r) => r['name']), ['W. Receiver1', 'W. Receiver2']);
    });

    test('basketball shows a decimal', () {
      final rows = _props(
        _predictionWithLeaders(home: [('c', 'Nikola Jokić', 'rebounding', {'rebounds': 12.84})]),
        {
          'rebounds': {'c': 2.06},
        },
        sport: SportIds.nba,
      );

      expect(rows.single, containsPair('value', '12.8'));
      expect(rows.single, containsPair('tolerance', '±2.1'));
      expect(rows.single, containsPair('route', '/nba/events/g1'));
    });

    test('a sport without player props has none', () {
      expect(
        _props(_predictionWithLeaders(), {}, sport: SportIds.pga),
        isEmpty,
      );
    });
  });

  group('fetchWidgetData player props', () {
    final events = {
      'events': [_game('g1', '2026-10-04', 'KC', 'JAX')],
    };
    final prediction = _predictionWithLeaders(away: [('qb', 'Patrick Mahomes', 'passing', {'passing_yards': 284})]);

    test('stores the props beside the picks for every picks widget', () async {
      final data = await fetchWidgetData(
        HomeWidgetKind.picksAndProps,
        sportById(SportIds.nfl),
        _api({
          '/nfl/events': events,
          '/nfl/predictions/events/g1': prediction,
          '/nfl/model-performance': _scorecardWithMisses({
            'passing-yards': {'qb': 38.0},
          }),
        }, []),
        now: _now,
      );

      expect((data['picks'] as List).single, containsPair('label', 'KC @ JAX'));
      expect((data['props'] as List).single, containsPair('name', 'P. Mahomes'));
    });

    test('a failed scorecard read keeps the picks, with no props', () async {
      final data = await fetchWidgetData(
        HomeWidgetKind.topProps,
        sportById(SportIds.nfl),
        _api({'/nfl/events': events, '/nfl/predictions/events/g1': prediction}, []),
        now: _now,
      );

      expect(data['picks'], hasLength(1));
      expect(data['props'], isEmpty);
    });

    test('skips the scorecard when no game has player projections', () async {
      final seen = <String>[];
      final data = await fetchWidgetData(
        HomeWidgetKind.topPicks,
        sportById(SportIds.nfl),
        _api({'/nfl/events': events, '/nfl/predictions/events/g1': _prediction(0.4)}, seen),
        now: _now,
      );

      expect(data['props'], isEmpty);
      expect(seen, isNot(contains('/nfl/model-performance')));
    });
  });

  group('fetchWidgetData top picks', () {
    test('ranks the day\'s games by the favorite\'s chance, top three', () async {
      final seen = <String>[];
      final data = await fetchWidgetData(
        HomeWidgetKind.topPicks,
        sportById(SportIds.nfl),
        _api({
          '/nfl/events': {
            'events': [
              _game('g1', '2026-10-04', 'KC', 'JAX'),
              _game('g2', '2026-10-04', 'DET', 'MIN'),
              _game('g3', '2026-10-04', 'SF', 'LAR'),
              _game('g4', '2026-10-04', 'NYJ', 'BUF'),
              _game('g5', '2026-10-11', 'DAL', 'PHI'),
            ],
          },
          '/nfl/predictions/events/g1': _prediction(0.32),
          '/nfl/predictions/events/g2': _prediction(0.39),
          '/nfl/predictions/events/g3': _prediction(0.53),
          '/nfl/predictions/events/g4': {'status': 'computing', 'retry_after_seconds': 5},
        }, seen),
        now: _now,
      );

      expect(data['heading'], 'Today');
      expect(data['picks'], [
        {'label': 'KC @ JAX', 'value': 'KC 68%', 'pct': closeTo(0.68, 1e-9)},
        {'label': 'DET @ MIN', 'value': 'DET 61%', 'pct': closeTo(0.61, 1e-9)},
        {'label': 'SF @ LAR', 'value': 'LAR 53%', 'pct': closeTo(0.53, 1e-9)},
      ]);
      expect(data['route'], AppRoutes.events(SportIds.nfl));
      expect(seen, isNot(contains('/nfl/predictions/events/g5')));
    });

    test('names the next game day when nothing is on today', () async {
      final data = await fetchWidgetData(
        HomeWidgetKind.topPicks,
        sportById(SportIds.nfl),
        _api({
          '/nfl/events': {'events': [_game('g5', '2026-10-11', 'DAL', 'PHI')]},
          '/nfl/predictions/events/g5': _prediction(0.7),
        }, []),
        now: _now,
      );

      expect(data['heading'], 'Sun Oct 11');
    });

    test('F1 ranks the next race\'s drivers by win chance', () async {
      final data = await fetchWidgetData(
        HomeWidgetKind.topPicks,
        sportById(SportIds.f1),
        _api({
          '/f1/events': {
            'events': [
              {'event_id': 'sprint', 'event_type': 'sprint', 'event_date': '2026-10-10'},
              {'event_id': 'race', 'event_type': 'field', 'event_date': '2026-10-11'},
            ],
          },
          '/f1/predictions/events/race': {
            'event_id': 'race',
            'race_name': 'United States Grand Prix',
            'field': [
              {'entity_id': 'd1', 'name': 'O. Piastri', 'predictions': {'win_probability': {'value': 0.22, 'model_version': 3}}},
              {'entity_id': 'd2', 'name': 'L. Norris', 'predictions': {'win_probability': {'value': 0.34, 'model_version': 3}}},
            ],
          },
        }, []),
        now: _now,
      );

      expect(data['heading'], 'United States Grand Prix');
      expect((data['picks'] as List).first, containsPair('value', 'Win · 34%'));
    });

    test('says so when nothing is scheduled', () async {
      final data = await fetchWidgetData(
        HomeWidgetKind.topPicks,
        sportById(SportIds.nba),
        _api({'/nba/events': {'events': []}}, []),
        now: _now,
      );

      expect(data['empty'], 'Nothing scheduled');
      expect(data['sport'], 'NBA');
    });
  });
}

import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/mobile/widget_data.dart';
import 'package:front_end/core/mobile/widget_sync.dart';
import 'package:front_end/core/models/model_performance.dart';
import 'package:front_end/core/models/sport_config.dart';
import 'package:front_end/core/routing/app_routes.dart';

import '../../support/model_performance_fixtures.dart';

final _now = DateTime.utc(2026, 10, 4, 15); // 11 AM Eastern, Sunday Oct 4

Map<String, dynamic> _game(String id, String date, String away, String home) => {
      'event_id': id,
      'event_date': date,
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
    expect(HomeWidgetKind.fromClassName('.Other'), isNull);
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
      expect(data['trend'], [0.62, 0.69, 0.75]);
      expect(data['route'], AppRoutes.performance(SportIds.nfl));
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

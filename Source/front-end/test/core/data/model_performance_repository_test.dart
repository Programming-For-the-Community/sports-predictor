import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

import 'package:front_end/core/data/model_performance_repository.dart';

import '../../support/api_client_test_support.dart';

Map<String, dynamic> _pickRecord() => {
      'model_name': 'win-probability',
      'version': 9,
      'kind': 'pick',
      'band_kind': 'confidence',
      'season': {'value': 0.684, 'n': 32},
      'last_period': {'label': 'Wk 3', 'value': 0.75, 'n': 12},
      'periods': [
        {'label': 'Wk 2', 'value': 0.69, 'n': 16},
        {'label': 'Wk 3', 'value': 0.75, 'n': 12},
      ],
      'vs_baseline_pct': 14.0,
      'at_training': 0.661,
      'margin_of_error': null,
      'bands': [
        {'tag': 'HIGH', 'lo': 0.13, 'hi': null, 'n': 9, 'pct': 0.89, 'early': false},
        {'tag': 'LOW', 'lo': 0.0, 'hi': 0.06, 'n': 3, 'pct': null, 'early': true},
      ],
    };

void main() {
  test('requests the sport-scoped model-performance route', () async {
    Uri? capturedUri;
    final repo = ModelPerformanceRepository(buildTestApiClient((request) async {
      capturedUri = request.url;
      return http.Response(jsonEncode({'sport': 'nfl', 'models': []}), 200);
    }));

    await repo.getModelPerformance('nfl');

    expect(capturedUri?.path, '/nfl/model-performance');
  });

  test('parses a pick record with its windows, periods and bands', () async {
    final repo = ModelPerformanceRepository(buildTestApiClient((request) async {
      return http.Response(
        jsonEncode({'sport': 'nfl', 'season': 2026, 'period_kind': 'week', 'models': [_pickRecord()]}),
        200,
      );
    }));

    final result = await repo.getModelPerformance('nfl');

    expect(result.season, 2026);
    expect(result.isWeekly, isTrue);
    final record = result.models.single;
    expect(record.isPick, isTrue);
    expect(record.hasResults, isTrue);
    expect(record.season.value, 0.684);
    expect(record.season.n, 32);
    expect(record.lastPeriod?.label, 'Wk 3');
    expect(record.periods.map((p) => p.label), ['Wk 2', 'Wk 3']);
    expect(record.vsBaselinePct, 14.0);
    expect(record.atTraining, 0.661);
    expect(record.bands.first.pct, 0.89);
    expect(record.bands.last.early, isTrue);
    expect(record.bands.last.pct, isNull);
  });

  test('parses the season and per-band lean of an amount model', () async {
    final record = {
      ..._pickRecord(),
      'kind': 'amount',
      'band_kind': 'predicted_amount',
      'bias': 2.4,
      'margin_of_error': 10.8,
      'bands': [
        {'tag': 'LOW', 'lo': 0.5, 'hi': 6.0, 'n': 11, 'pct': 0.64, 'early': false, 'bias': -3.1},
        {'tag': 'HIGH', 'lo': 11.5, 'hi': 17.0, 'n': 2, 'pct': null, 'early': true, 'bias': null},
      ],
    };
    final repo = ModelPerformanceRepository(buildTestApiClient((request) async {
      return http.Response(jsonEncode({'sport': 'nfl', 'models': [record]}), 200);
    }));

    final parsed = (await repo.getModelPerformance('nfl')).models.single;

    expect(parsed.bias, 2.4);
    expect(parsed.bands.first.bias, -3.1);
    expect(parsed.bands.last.bias, isNull);
  });

  test('parses a chance model with the sport-supplied noun', () async {
    final record = {
      ..._pickRecord(),
      'model_name': 'top-10-probability',
      'kind': 'chance',
      'band_kind': 'predicted_chance',
      'count_noun': 'golfers',
    };
    final repo = ModelPerformanceRepository(buildTestApiClient((request) async {
      return http.Response(jsonEncode({'sport': 'pga', 'period_kind': 'event', 'models': [record]}), 200);
    }));

    final result = await repo.getModelPerformance('pga');

    expect(result.isWeekly, isFalse);
    expect(result.models.single.kind, 'chance');
    expect(result.models.single.isAmount, isFalse);
    expect(result.models.single.countNoun, 'golfers');
  });

  test('parses the rolling window a sport is graded on', () async {
    final repo = ModelPerformanceRepository(buildTestApiClient((request) async {
      return http.Response(jsonEncode({'sport': 'ncaambb', 'window_days': 7, 'models': []}), 200);
    }));

    expect((await repo.getModelPerformance('ncaambb')).windowDays, 7);
  });

  test('an empty scorecard (before the first daily run) parses to no models', () async {
    final repo = ModelPerformanceRepository(buildTestApiClient((request) async {
      return http.Response(jsonEncode({'sport': 'nfl', 'models': []}), 200);
    }));

    final result = await repo.getModelPerformance('nfl');

    expect(result.models, isEmpty);
    expect(result.season, isNull);
  });

  test('a record with no graded predictions has no results and no last period', () async {
    final record = {
      ..._pickRecord(),
      'season': {'value': null, 'n': 0},
      'last_period': null,
      'periods': [],
      'vs_baseline_pct': null,
      'bands': [],
    };
    final repo = ModelPerformanceRepository(buildTestApiClient((request) async {
      return http.Response(jsonEncode({'sport': 'nfl', 'models': [record]}), 200);
    }));

    final parsed = (await repo.getModelPerformance('nfl')).models.single;

    expect(parsed.hasResults, isFalse);
    expect(parsed.lastPeriod, isNull);
    expect(parsed.bands, isEmpty);
  });
}

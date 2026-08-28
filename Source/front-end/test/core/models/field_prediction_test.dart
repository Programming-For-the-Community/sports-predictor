import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/models/field_prediction.dart';

Map<String, dynamic> _fieldResponse() => {
      'sport': 'pga', 'event_id': '401811963', 'event_type': 'field',
      'tournament_name': 'BMW Championship', 'status': 'scheduled',
      'cutline': {
        'projected_cut_score': {'value': -2.0, 'model_version': 3},
      },
      'field': [
        {
          'entity_id': '10140', 'name': 'Xander Schauffele', 'country': 'USA',
          'predictions': {
            'top_10_probability': {'value': 0.42, 'model_version': 1},
            'top_5_probability': {'value': 0.21, 'model_version': 1},
            'projected_score_to_par': {'value': -8.5, 'model_version': 2},
            'rounds': {
              'round_2': {'value': -3.0, 'model_version': 1},
            },
          },
        },
      ],
    };

Map<String, dynamic> _twoSidedResponse(String eventType) => {
      'sport': 'pga', 'event_id': '401465497-match-10951', 'event_type': eventType,
      'tournament_name': 'Presidents Cup', 'status': 'scheduled',
      'match_format': 'Foursomes', 'session_name': 'Session 1',
      'home': {'entity_id': '1', 'name': 'United States', 'golfers': [
        {'entity_id': '1085', 'name': 'Scottie Scheffler', 'country': 'USA'},
      ]},
      'away': {'entity_id': '3', 'name': 'International'},
      'predictions': {
        (eventType == 'cup' ? 'cup_win_probability' : 'match_win_probability'): {'value': 0.58, 'model_version': 1},
      },
    };

void main() {
  group('parsePgaEventPrediction dispatch', () {
    test('field event_type parses into a PgaFieldPrediction', () {
      final result = parsePgaEventPrediction(_fieldResponse());

      expect(result, isA<PgaFieldPrediction>());
      expect((result as PgaFieldPrediction).prediction.tournamentName, 'BMW Championship');
    });

    test('match_play event_type parses into a PgaTwoSidedPrediction', () {
      final result = parsePgaEventPrediction(_twoSidedResponse('match_play'));

      expect(result, isA<PgaTwoSidedPrediction>());
      expect((result as PgaTwoSidedPrediction).prediction.eventType, 'match_play');
    });

    test('cup event_type parses into a PgaTwoSidedPrediction', () {
      final result = parsePgaEventPrediction(_twoSidedResponse('cup'));

      expect(result, isA<PgaTwoSidedPrediction>());
      expect((result as PgaTwoSidedPrediction).prediction.winProbability!.value, 0.58);
    });

    test('an unrecognized event_type throws FormatException', () {
      final json = _fieldResponse()..['event_type'] = 'nonsense';

      expect(() => parsePgaEventPrediction(json), throwsFormatException);
    });
  });

  group('FieldEventPrediction', () {
    test('parses cutline and field entries', () {
      final prediction = FieldEventPrediction.fromJson(_fieldResponse());

      expect(prediction.cutline!.projectedCutScore!.value, -2.0);
      expect(prediction.field.single.top10Probability!.value, 0.42);
      expect(prediction.field.single.rounds[2]!.value, -3.0);
    });

    test('a model with no promoted version yet is independently null, not all-or-nothing', () {
      final json = _fieldResponse();
      (json['field'] as List)[0]['predictions'].remove('top_5_probability');

      final prediction = FieldEventPrediction.fromJson(json);

      expect(prediction.field.single.top5Probability, isNull);
      expect(prediction.field.single.top10Probability, isNotNull);
    });

    test('actual result is only present when the participant carries one', () {
      final json = _fieldResponse();
      (json['field'] as List)[0]['actual'] = {'finish_position': 26, 'score_to_par': -4};

      final prediction = FieldEventPrediction.fromJson(json);

      expect(prediction.field.single.actualFinishPosition, 26);
      expect(prediction.field.single.actualScoreToPar, -4);
    });

    test('par is parsed at the top level', () {
      final json = _fieldResponse();
      json['par'] = 70;

      final prediction = FieldEventPrediction.fromJson(json);

      expect(prediction.par, 70);
    });

    test('par is null when the response omits it', () {
      final prediction = FieldEventPrediction.fromJson(_fieldResponse());

      expect(prediction.par, isNull);
    });

    test('actual total strokes is parsed alongside score to par', () {
      final json = _fieldResponse();
      (json['field'] as List)[0]['actual'] = {'finish_position': 26, 'score_to_par': -4, 'total_strokes': 276.0};

      final prediction = FieldEventPrediction.fromJson(json);

      expect(prediction.field.single.actualTotalStrokes, 276.0);
    });

    test('actual rounds are parsed and keyed by round number, not status-gated', () {
      final json = _fieldResponse();
      (json['field'] as List)[0]['actual'] = {
        'finish_position': null, 'score_to_par': null,
        'rounds': [
          {'round': 1, 'score_to_par': -4, 'total_strokes': 68.0},
        ],
      };

      final prediction = FieldEventPrediction.fromJson(json);

      expect(prediction.field.single.actualRounds[1]!.scoreToPar, -4);
      expect(prediction.field.single.actualRounds[1]!.totalStrokes, 68.0);
    });

    test('actual thru is parsed alongside status', () {
      final json = _fieldResponse();
      (json['field'] as List)[0]['actual'] = {'finish_position': 5, 'score_to_par': -3, 'status': 'in_progress', 'thru': 14};

      final prediction = FieldEventPrediction.fromJson(json);

      expect(prediction.field.single.actualThru, 14);
      expect(prediction.field.single.actualStatus, 'in_progress');
    });

    test('actualThru is null when actual is absent', () {
      final prediction = FieldEventPrediction.fromJson(_fieldResponse());

      expect(prediction.field.single.actualThru, isNull);
    });

    test('actualRounds defaults to empty when actual is absent', () {
      final prediction = FieldEventPrediction.fromJson(_fieldResponse());

      expect(prediction.field.single.actualRounds, isEmpty);
    });

    test('stale and retry_after_seconds default safely when absent', () {
      final prediction = FieldEventPrediction.fromJson(_fieldResponse());

      expect(prediction.stale, isFalse);
      expect(prediction.staleRetryAfterSeconds, isNull);
    });
  });

  group('TwoSidedPgaPrediction', () {
    test('parses home/away sides including nested golfers', () {
      final prediction = TwoSidedPgaPrediction.fromJson(_twoSidedResponse('match_play'));

      expect(prediction.home!.name, 'United States');
      expect(prediction.home!.golfers!.single.name, 'Scottie Scheffler');
      expect(prediction.away!.golfers, isNull);
    });

    test('winProbability is null when predictions is empty', () {
      final json = _twoSidedResponse('match_play')..['predictions'] = <String, dynamic>{};

      final prediction = TwoSidedPgaPrediction.fromJson(json);

      expect(prediction.winProbability, isNull);
    });

    test('actual result is only present when status is completed', () {
      final json = _twoSidedResponse('cup');
      json['status'] = 'completed';
      json['actual'] = {'home_won': true, 'halved': false};

      final prediction = TwoSidedPgaPrediction.fromJson(json);

      expect(prediction.actualHomeWon, isTrue);
      expect(prediction.actualHalved, isFalse);
    });
  });
}

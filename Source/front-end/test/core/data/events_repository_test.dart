import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

import 'package:front_end/core/data/events_repository.dart';

import '../../support/api_client_test_support.dart';

Map<String, dynamic> _event({String eventId = '1'}) => {
      'event_id': eventId,
      'event_date': '2025-09-28',
      'status': 'scheduled',
      'participants': [
        {'entity_id': 'KC', 'role': 'home'},
        {'entity_id': 'LAC', 'role': 'away'},
      ],
    };

Map<String, dynamic> _predictionJson() => {
      'predictions': {
        'win_probability': {'home_win_probability': 0.6, 'model_version': 3},
        'margin': {'value': 4.5},
        'home_score': {'value': 24.0},
        'away_score': {'value': 19.5},
      },
    };

void main() {
  group('listEvents', () {
    test('requests the sport-scoped events route with the status query param', () async {
      Uri? capturedUri;
      final repo = EventsRepository(buildTestApiClient((request) async {
        capturedUri = request.url;
        return http.Response(jsonEncode({'events': []}), 200);
      }));

      await repo.listEvents('nfl', status: 'completed');

      expect(capturedUri?.path, '/nfl/events');
      expect(capturedUri?.queryParameters['status'], 'completed');
    });

    test('parses every event in the response', () async {
      final repo = EventsRepository(buildTestApiClient((request) async {
        return http.Response(jsonEncode({'events': [_event(eventId: '1'), _event(eventId: '2')]}), 200);
      }));

      final events = await repo.listEvents('nfl');

      expect(events.map((e) => e.eventId), ['1', '2']);
    });

    test('defaults to an empty list when the events key is missing', () async {
      final repo = EventsRepository(buildTestApiClient((request) async => http.Response('{}', 200)));

      final events = await repo.listEvents('nfl');

      expect(events, isEmpty);
    });
  });

  group('getEventPrediction', () {
    test('requests the event-scoped prediction route', () async {
      Uri? capturedUri;
      final repo = EventsRepository(buildTestApiClient((request) async {
        capturedUri = request.url;
        return http.Response(jsonEncode(_predictionJson()), 200);
      }));

      await repo.getEventPrediction('nfl', '401547417');

      expect(capturedUri?.path, '/nfl/predictions/events/401547417');
    });

    test('parses a real prediction response', () async {
      final repo = EventsRepository(buildTestApiClient((request) async => http.Response(jsonEncode(_predictionJson()), 200)));

      final prediction = await repo.getEventPrediction('nfl', '401547417');

      expect(prediction.homeWinProbability, 0.6);
      expect(prediction.margin, 4.5);
    });

    test('throws PredictionComputingException on a computing response, with the retry hint', () async {
      final repo = EventsRepository(buildTestApiClient((request) async {
        return http.Response(jsonEncode({'status': 'computing', 'retry_after_seconds': 7}), 200);
      }));

      await expectLater(
        repo.getEventPrediction('nfl', '401547417'),
        throwsA(isA<PredictionComputingException>().having((e) => e.retryAfterSeconds, 'retryAfterSeconds', 7)),
      );
    });

    test('defaults the retry hint to 5 seconds when the server omits it', () async {
      final repo = EventsRepository(buildTestApiClient((request) async => http.Response(jsonEncode({'status': 'computing'}), 200)));

      await expectLater(
        repo.getEventPrediction('nfl', '401547417'),
        throwsA(isA<PredictionComputingException>().having((e) => e.retryAfterSeconds, 'retryAfterSeconds', 5)),
      );
    });
  });
}

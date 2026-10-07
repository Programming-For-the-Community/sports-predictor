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

  group('predictions embedded in the events list', () {
    test('a listed prediction is served without a prediction request', () async {
      final paths = <String>[];
      final container = buildTestContainer((request) async {
        paths.add(request.url.path);
        return http.Response(jsonEncode({'events': [{..._event(eventId: '7'), 'prediction': _predictionJson()}]}), 200);
      });
      addTearDown(container.dispose);

      await container.read(eventsListProvider((sport: 'nfl', status: 'scheduled')).future);
      final prediction = await container.read(eventPredictionProvider((sport: 'nfl', eventId: '7')).future);

      expect(prediction.homeWinProbability, 0.6);
      expect(paths, ['/nfl/events']);
    });

    test('refreshing that prediction afterwards asks the prediction route', () async {
      final paths = <String>[];
      final container = buildTestContainer((request) async {
        paths.add(request.url.path);
        if (request.url.path == '/nfl/events') {
          return http.Response(jsonEncode({'events': [{..._event(eventId: '7'), 'prediction': _predictionJson()}]}), 200);
        }
        return http.Response(jsonEncode(_predictionJson()), 200);
      });
      addTearDown(container.dispose);
      final predictionProvider = eventPredictionProvider((sport: 'nfl', eventId: '7'));

      await container.read(eventsListProvider((sport: 'nfl', status: 'scheduled')).future);
      await container.read(predictionProvider.future);
      await container.refresh(predictionProvider.future);

      expect(paths, ['/nfl/events', '/nfl/predictions/events/7']);
    });

    test('an event listed without a prediction still asks the prediction route', () async {
      final paths = <String>[];
      final container = buildTestContainer((request) async {
        paths.add(request.url.path);
        if (request.url.path == '/nfl/events') {
          return http.Response(jsonEncode({'events': [{..._event(eventId: '7'), 'prediction': null}]}), 200);
        }
        return http.Response(jsonEncode(_predictionJson()), 200);
      });
      addTearDown(container.dispose);

      await container.read(eventsListProvider((sport: 'nfl', status: 'scheduled')).future);
      await container.read(eventPredictionProvider((sport: 'nfl', eventId: '7')).future);

      expect(paths, ['/nfl/events', '/nfl/predictions/events/7']);
    });

    test('a refreshed list replaces a prediction already on screen', () async {
      var homeWinProbability = 0.6;
      final container = buildTestContainer((request) async {
        final prediction = _predictionJson();
        ((prediction['predictions'] as Map)['win_probability'] as Map)['home_win_probability'] = homeWinProbability;
        return http.Response(jsonEncode({'events': [{..._event(eventId: '7'), 'prediction': prediction}]}), 200);
      });
      addTearDown(container.dispose);
      final listProvider = eventsListProvider((sport: 'nfl', status: 'scheduled'));
      final predictionProvider = eventPredictionProvider((sport: 'nfl', eventId: '7'));
      container.listen(predictionProvider, (_, __) {});

      await container.read(listProvider.future);
      expect((await container.read(predictionProvider.future)).homeWinProbability, 0.6);

      homeWinProbability = 0.7;
      await container.refresh(listProvider.future);

      expect((await container.read(predictionProvider.future)).homeWinProbability, 0.7);
    });
  });
}

import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

import 'package:front_end/core/data/f1_events_repository.dart';

import '../../support/api_client_test_support.dart';

Map<String, dynamic> _event({String eventId = '1'}) => {
      'event_id': eventId,
      'event_date': '2026-08-23',
      'status': 'scheduled',
      'participants': [
        {'entity_id': 'ver'},
      ],
    };

void main() {
  group('listEvents', () {
    test('requests the sport-scoped events route with the status query param', () async {
      Uri? capturedUri;
      final repo = F1EventsRepository(buildTestApiClient((request) async {
        capturedUri = request.url;
        return http.Response(jsonEncode({'events': []}), 200);
      }));

      await repo.listEvents('f1', status: 'completed');

      expect(capturedUri?.path, '/f1/events');
      expect(capturedUri?.queryParameters['status'], 'completed');
    });

    test('parses every event in the response', () async {
      final repo = F1EventsRepository(buildTestApiClient((request) async {
        return http.Response(jsonEncode({'events': [_event(eventId: '1'), _event(eventId: '2')]}), 200);
      }));

      final events = await repo.listEvents('f1');

      expect(events.map((e) => e.eventId), ['1', '2']);
    });

    test('defaults to an empty list when the events key is missing', () async {
      final repo = F1EventsRepository(buildTestApiClient((request) async => http.Response('{}', 200)));

      expect(await repo.listEvents('f1'), isEmpty);
    });
  });

  group('getEventPrediction', () {
    test('requests the event-scoped prediction route', () async {
      Uri? capturedUri;
      final repo = F1EventsRepository(buildTestApiClient((request) async {
        capturedUri = request.url;
        return http.Response(jsonEncode({'event_id': '1', 'event_type': 'field', 'field': []}), 200);
      }));

      await repo.getEventPrediction('f1', '1197');

      expect(capturedUri?.path, '/f1/predictions/events/1197');
    });

    test('parses a real prediction response', () async {
      final repo = F1EventsRepository(buildTestApiClient((request) async {
        return http.Response(jsonEncode({'event_id': '1', 'event_type': 'sprint', 'field': []}), 200);
      }));

      final prediction = await repo.getEventPrediction('f1', '1197');

      expect(prediction.isSprint, isTrue);
    });

    test('throws PredictionComputingException on a computing response', () async {
      final repo = F1EventsRepository(buildTestApiClient((request) async {
        return http.Response(jsonEncode({'status': 'computing', 'retry_after_seconds': 3}), 200);
      }));

      await expectLater(
        repo.getEventPrediction('f1', '1197'),
        throwsA(isA<PredictionComputingException>().having((e) => e.retryAfterSeconds, 'retryAfterSeconds', 3)),
      );
    });
  });

  group('providers', () {
    test('f1EventsListProvider resolves through f1EventsRepositoryProvider', () async {
      final container = buildTestContainer((request) async {
        return http.Response(jsonEncode({'events': [_event(eventId: '1')]}), 200);
      });
      addTearDown(container.dispose);

      final events = await container.read(f1EventsListProvider((sport: 'f1', status: 'scheduled')).future);

      expect(events.map((e) => e.eventId), ['1']);
    });

    test('f1EventPredictionProvider resolves through f1EventsRepositoryProvider', () async {
      final container = buildTestContainer((request) async {
        return http.Response(jsonEncode({'event_id': '1197', 'event_type': 'field', 'field': []}), 200);
      });
      addTearDown(container.dispose);

      final prediction = await container.read(f1EventPredictionProvider((sport: 'f1', eventId: '1197')).future);

      expect(prediction.isSprint, isFalse);
    });
  });
}

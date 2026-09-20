import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

import 'package:front_end/core/data/field_events_repository.dart';
import 'package:front_end/core/models/field_prediction.dart';

import '../../support/api_client_test_support.dart';

Map<String, dynamic> _event({String eventId = '1'}) => {
      'event_id': eventId,
      'event_date': '2026-08-20',
      'status': 'scheduled',
      'participants': [
        {'entity_id': 'p1'},
      ],
    };

void main() {
  group('listEvents', () {
    test('requests the sport-scoped events route with the status query param', () async {
      Uri? capturedUri;
      final repo = FieldEventsRepository(buildTestApiClient((request) async {
        capturedUri = request.url;
        return http.Response(jsonEncode({'events': []}), 200);
      }));

      await repo.listEvents('pga', status: 'completed');

      expect(capturedUri?.path, '/pga/events');
      expect(capturedUri?.queryParameters['status'], 'completed');
    });

    test('parses every event in the response', () async {
      final repo = FieldEventsRepository(buildTestApiClient((request) async {
        return http.Response(jsonEncode({'events': [_event(eventId: '1'), _event(eventId: '2')]}), 200);
      }));

      final events = await repo.listEvents('pga');

      expect(events.map((e) => e.eventId), ['1', '2']);
    });

    test('defaults to an empty list when the events key is missing', () async {
      final repo = FieldEventsRepository(buildTestApiClient((request) async => http.Response('{}', 200)));

      expect(await repo.listEvents('pga'), isEmpty);
    });
  });

  group('getEventPrediction', () {
    test('requests the event-scoped prediction route', () async {
      Uri? capturedUri;
      final repo = FieldEventsRepository(buildTestApiClient((request) async {
        capturedUri = request.url;
        return http.Response(jsonEncode({'event_id': '1', 'event_type': 'field', 'field': []}), 200);
      }));

      await repo.getEventPrediction('pga', '401811963');

      expect(capturedUri?.path, '/pga/predictions/events/401811963');
    });

    test('dispatches a field-typed response to PgaFieldPrediction', () async {
      final repo = FieldEventsRepository(buildTestApiClient((request) async {
        return http.Response(jsonEncode({'event_id': '1', 'event_type': 'field', 'field': []}), 200);
      }));

      final prediction = await repo.getEventPrediction('pga', '401811963');

      expect(prediction, isA<PgaFieldPrediction>());
    });

    test('dispatches a match_play-typed response to PgaTwoSidedPrediction', () async {
      final repo = FieldEventsRepository(buildTestApiClient((request) async {
        return http.Response(jsonEncode({'event_id': '1', 'event_type': 'match_play'}), 200);
      }));

      final prediction = await repo.getEventPrediction('pga', '401465497');

      expect(prediction, isA<PgaTwoSidedPrediction>());
    });

    test('throws PredictionComputingException on a computing response', () async {
      final repo = FieldEventsRepository(buildTestApiClient((request) async {
        return http.Response(jsonEncode({'status': 'computing', 'retry_after_seconds': 9}), 200);
      }));

      await expectLater(
        repo.getEventPrediction('pga', '401811963'),
        throwsA(isA<PredictionComputingException>().having((e) => e.retryAfterSeconds, 'retryAfterSeconds', 9)),
      );
    });
  });
}

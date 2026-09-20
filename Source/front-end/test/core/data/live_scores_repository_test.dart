import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

import 'package:front_end/core/data/live_scores_repository.dart';
import 'package:front_end/core/models/field_live_score.dart';

import '../../support/api_client_test_support.dart';

void main() {
  group('getLiveScores', () {
    test('requests the sport-scoped live-scores route', () async {
      Uri? capturedUri;
      final repo = LiveScoresRepository(buildTestApiClient((request) async {
        capturedUri = request.url;
        return http.Response(jsonEncode({'events': {}}), 200);
      }));

      await repo.getLiveScores('nfl');

      expect(capturedUri?.path, '/nfl/live-scores');
    });

    test('parses each entry keyed by event id', () async {
      final repo = LiveScoresRepository(buildTestApiClient((request) async {
        return http.Response(
          jsonEncode({
            'events': {
              '1': {'live': true, 'detail': 'Q3 08:14'},
            },
          }),
          200,
        );
      }));

      final result = await repo.getLiveScores('nfl');

      expect(result['1']!.live, isTrue);
      expect(result['1']!.detail, 'Q3 08:14');
    });

    test('defaults to an empty map when the events key is missing', () async {
      final repo = LiveScoresRepository(buildTestApiClient((request) async => http.Response('{}', 200)));

      expect(await repo.getLiveScores('nfl'), isEmpty);
    });
  });

  group('getPgaLiveScores', () {
    test('requests the sport-scoped live-scores route', () async {
      Uri? capturedUri;
      final repo = LiveScoresRepository(buildTestApiClient((request) async {
        capturedUri = request.url;
        return http.Response(jsonEncode({'events': {}}), 200);
      }));

      await repo.getPgaLiveScores('pga');

      expect(capturedUri?.path, '/pga/live-scores');
    });

    test('dispatches a field-typed entry to PgaFieldLiveState', () async {
      final repo = LiveScoresRepository(buildTestApiClient((request) async {
        return http.Response(
          jsonEncode({
            'events': {
              '1': {'event_type': 'field', 'status': 'scheduled'},
            },
          }),
          200,
        );
      }));

      final result = await repo.getPgaLiveScores('pga');

      expect(result['1'], isA<PgaFieldLiveState>());
    });

    test('dispatches a match_play-typed entry to PgaTwoSidedLiveState', () async {
      final repo = LiveScoresRepository(buildTestApiClient((request) async {
        return http.Response(
          jsonEncode({
            'events': {
              '1': {'event_type': 'match_play', 'status': 'scheduled'},
            },
          }),
          200,
        );
      }));

      final result = await repo.getPgaLiveScores('pga');

      expect(result['1'], isA<PgaTwoSidedLiveState>());
    });
  });

  group('getFieldLiveScores', () {
    test('filters PGA live scores down to just the field-typed entries', () async {
      final repo = LiveScoresRepository(buildTestApiClient((request) async {
        return http.Response(
          jsonEncode({
            'events': {
              'field-event': {'event_type': 'field', 'status': 'scheduled'},
              'match-event': {'event_type': 'match_play', 'status': 'scheduled'},
            },
          }),
          200,
        );
      }));

      final result = await repo.getFieldLiveScores('pga');

      expect(result.keys, ['field-event']);
    });
  });

  group('getF1LiveScores', () {
    test('requests the sport-scoped live-scores route', () async {
      Uri? capturedUri;
      final repo = LiveScoresRepository(buildTestApiClient((request) async {
        capturedUri = request.url;
        return http.Response(jsonEncode({'events': {}}), 200);
      }));

      await repo.getF1LiveScores('f1');

      expect(capturedUri?.path, '/f1/live-scores');
    });

    test('parses each entry keyed by event id', () async {
      final repo = LiveScoresRepository(buildTestApiClient((request) async {
        return http.Response(
          jsonEncode({
            'events': {
              '1197': {'event_type': 'field', 'state': 'in'},
            },
          }),
          200,
        );
      }));

      final result = await repo.getF1LiveScores('f1');

      expect(result['1197']!.isLive, isTrue);
    });

    test('defaults to an empty map when the events key is missing', () async {
      final repo = LiveScoresRepository(buildTestApiClient((request) async => http.Response('{}', 200)));

      expect(await repo.getF1LiveScores('f1'), isEmpty);
    });
  });
}

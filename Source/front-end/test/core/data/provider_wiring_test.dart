import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

import 'package:front_end/core/data/events_repository.dart';
import 'package:front_end/core/data/field_events_repository.dart';
import 'package:front_end/core/data/live_scores_repository.dart';
import 'package:front_end/core/data/model_performance_repository.dart';

import '../../support/api_client_test_support.dart';

/// Each FutureProvider reads through its own repository provider and the
/// real ApiClient, down to the stubbed HTTP handler.
void main() {
  late List<Uri> requests;

  setUp(() => requests = []);

  Future<http.Response> handler(http.Request request, Object body) async {
    requests.add(request.url);
    return http.Response(jsonEncode(body), 200);
  }

  test('eventsListProvider lists events for its sport and status', () async {
    final container = buildTestContainer((r) => handler(r, {'events': []}));
    addTearDown(container.dispose);

    final events = await container.read(eventsListProvider((sport: 'nfl', status: 'completed')).future);

    expect(events, isEmpty);
    expect(requests.single.path, '/nfl/events');
    expect(requests.single.queryParameters['status'], 'completed');
  });

  test('fieldChildEventsProvider lists a cup event\'s child matches', () async {
    final container = buildTestContainer((r) => handler(r, {'events': []}));
    addTearDown(container.dispose);

    await container.read(fieldChildEventsProvider((sport: 'pga', eventId: 'cup-1')).future);

    expect(requests.single.queryParameters['parent_event_id'], 'cup-1');
  });

  test('liveScoresProvider and fieldLiveScoresProvider read the live-scores route', () async {
    final container = buildTestContainer((r) => handler(r, {'events': {}}));
    addTearDown(container.dispose);

    expect(await container.read(liveScoresProvider('nfl').future), isEmpty);
    expect(await container.read(fieldLiveScoresProvider('pga').future), isEmpty);
    expect(requests.map((u) => u.path), ['/nfl/live-scores', '/pga/live-scores']);
  });

  test('modelPerformanceProvider reads the model-performance route', () async {
    final container = buildTestContainer((r) => handler(r, {'sport': 'nfl', 'models': []}));
    addTearDown(container.dispose);

    final performance = await container.read(modelPerformanceProvider('nfl').future);

    expect(performance.models, isEmpty);
    expect(requests.single.path, '/nfl/model-performance');
  });
}

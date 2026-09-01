import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/api_client.dart';
import '../models/event_status.dart';
import '../models/f1_event.dart';
import '../models/f1_prediction.dart';
import 'events_repository.dart' show PredictionComputingException;

export 'events_repository.dart' show PredictionComputingException;

/// F1's own repository, parallel to FieldEventsRepository (PGA's own,
/// field_events_repository.dart) rather than folded into it -- F1's
/// /events and /predictions responses are a genuinely different shape
/// (f1_event.dart/f1_prediction.dart: driver+constructor, field/sprint
/// event_types, no rounds/cutline/score-to-par at all), not a variant of
/// PGA's own field-event shape despite both sharing EventShape.field.
class F1EventsRepository {
  F1EventsRepository(this._api);

  final ApiClient _api;

  Future<List<F1Event>> listEvents(String sport, {String status = EventStatus.scheduled}) async {
    final response = await _api.get('/$sport/events', queryParameters: {'status': status}) as Map<String, dynamic>;
    final events = response['events'] as List<dynamic>? ?? [];
    return events.map((e) => F1Event.fromJson(e as Map<String, dynamic>)).toList();
  }

  Future<F1EventPrediction> getEventPrediction(String sport, String eventId) async {
    final response = await _api.get('/$sport/predictions/events/$eventId') as Map<String, dynamic>;
    // Same "computing" cache-miss shape as every other sport's predict
    // route -- reuses EventsRepository's own exception type rather than
    // duplicating it, since callers already catch this type generically.
    if (response['status'] == 'computing') {
      throw PredictionComputingException(response['retry_after_seconds'] as int? ?? 5);
    }
    return F1EventPrediction.fromJson(response);
  }
}

final f1EventsRepositoryProvider =
    Provider<F1EventsRepository>((ref) => F1EventsRepository(ref.watch(apiClientProvider)));

typedef _F1EventsQuery = ({String sport, String status});

final f1EventsListProvider = FutureProvider.family<List<F1Event>, _F1EventsQuery>((ref, query) {
  return ref.watch(f1EventsRepositoryProvider).listEvents(query.sport, status: query.status);
});

typedef _F1EventQuery = ({String sport, String eventId});

final f1EventPredictionProvider = FutureProvider.family<F1EventPrediction, _F1EventQuery>((ref, query) {
  return ref.watch(f1EventsRepositoryProvider).getEventPrediction(query.sport, query.eventId);
});

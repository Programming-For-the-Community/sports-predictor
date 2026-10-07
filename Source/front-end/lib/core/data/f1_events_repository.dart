import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/api_client.dart';
import '../api/api_routes.dart';
import '../models/event_status.dart';
import '../models/f1_event.dart';
import '../models/f1_prediction.dart';
import 'events_repository.dart' show PredictionComputingException;
import 'last_known_cache.dart';

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

  /// The raw GET /{sport}/events body -- see [parseEvents].
  Future<Object?> fetchEvents(String sport, {String status = EventStatus.scheduled}) =>
      _api.get(ApiRoutes.events(sport), queryParameters: {'status': status});

  static List<F1Event> parseEvents(Object? json) {
    final events = (json as Map<String, dynamic>)['events'] as List<dynamic>? ?? [];
    return events.map((e) => F1Event.fromJson(e as Map<String, dynamic>)).toList();
  }

  Future<List<F1Event>> listEvents(String sport, {String status = EventStatus.scheduled}) async =>
      parseEvents(await fetchEvents(sport, status: status));

  /// The raw GET /{sport}/predictions/events/{event_id} body -- see [parsePrediction].
  Future<Object?> fetchEventPrediction(String sport, String eventId) => _api.get(ApiRoutes.eventPrediction(sport, eventId));

  static F1EventPrediction parsePrediction(Object? json) {
    final response = json as Map<String, dynamic>;
    // Same "computing" cache-miss shape as every other sport's predict
    // route -- reuses EventsRepository's own exception type rather than
    // duplicating it, since callers already catch this type generically.
    if (response['status'] == 'computing') {
      throw PredictionComputingException(response['retry_after_seconds'] as int? ?? 5);
    }
    return F1EventPrediction.fromJson(response);
  }

  Future<F1EventPrediction> getEventPrediction(String sport, String eventId) async =>
      parsePrediction(await fetchEventPrediction(sport, eventId));
}

final f1EventsRepositoryProvider =
    Provider<F1EventsRepository>((ref) => F1EventsRepository(ref.watch(apiClientProvider)));

typedef _F1EventsQuery = ({String sport, String status});

final f1EventsListProvider = FutureProvider.family<List<F1Event>, _F1EventsQuery>((ref, query) {
  final repository = ref.watch(f1EventsRepositoryProvider);
  return lastKnownThenFresh(
    ref,
    key: 'events/${query.sport}/${query.status}',
    fetch: () => repository.fetchEvents(query.sport, status: query.status),
    parse: F1EventsRepository.parseEvents,
  );
});

typedef _F1EventQuery = ({String sport, String eventId});

final f1EventPredictionProvider = FutureProvider.family<F1EventPrediction, _F1EventQuery>((ref, query) {
  final repository = ref.watch(f1EventsRepositoryProvider);
  return lastKnownThenFresh(
    ref,
    key: 'prediction/${query.sport}/${query.eventId}',
    fetch: () => repository.fetchEventPrediction(query.sport, query.eventId),
    parse: F1EventsRepository.parsePrediction,
  );
});

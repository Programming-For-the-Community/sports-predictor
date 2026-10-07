import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/api_client.dart';
import '../api/api_routes.dart';
import '../models/event_status.dart';
import '../models/field_event.dart';
import '../models/field_prediction.dart';
import 'events_repository.dart' show PredictionComputingException;
import 'last_known_cache.dart';

export 'events_repository.dart' show PredictionComputingException;

/// PGA's own repository, parallel to EventsRepository (events_repository.dart)
/// rather than folded into it -- PGA's /events and /predictions responses
/// are genuinely different shapes (field_event.dart/field_prediction.dart),
/// not variants of SportEvent/EventPrediction.
class FieldEventsRepository {
  FieldEventsRepository(this._api);

  final ApiClient _api;

  /// The raw GET /{sport}/events body -- see [parseEvents].
  Future<Object?> fetchEvents(String sport, {String status = EventStatus.scheduled}) =>
      _api.get(ApiRoutes.events(sport), queryParameters: {'status': status});

  static List<FieldEvent> parseEvents(Object? json) {
    final events = (json as Map<String, dynamic>)['events'] as List<dynamic>? ?? [];
    return events.map((e) => FieldEvent.fromJson(e as Map<String, dynamic>)).toList();
  }

  Future<List<FieldEvent>> listEvents(String sport, {String status = EventStatus.scheduled}) async =>
      parseEvents(await fetchEvents(sport, status: status));

  /// A Ryder Cup/Presidents Cup's own matches, in tee-off order.
  Future<List<FieldEvent>> listChildEvents(String sport, String parentEventId) async {
    final response =
        await _api.get(ApiRoutes.events(sport), queryParameters: {'parent_event_id': parentEventId}) as Map<String, dynamic>;
    final events = response['events'] as List<dynamic>? ?? [];
    return events.map((e) => FieldEvent.fromJson(e as Map<String, dynamic>)).toList();
  }

  /// The raw GET /{sport}/predictions/events/{event_id} body -- see [parsePrediction].
  Future<Object?> fetchEventPrediction(String sport, String eventId) => _api.get(ApiRoutes.eventPrediction(sport, eventId));

  static PgaEventPrediction parsePrediction(Object? json) {
    final response = json as Map<String, dynamic>;
    // Same "computing" cache-miss shape as every other sport's predict
    // route -- reuses EventsRepository's own exception type rather than
    // duplicating it, since callers already catch this type generically.
    if (response['status'] == 'computing') {
      throw PredictionComputingException(response['retry_after_seconds'] as int? ?? 5);
    }
    return parsePgaEventPrediction(response);
  }

  Future<PgaEventPrediction> getEventPrediction(String sport, String eventId) async =>
      parsePrediction(await fetchEventPrediction(sport, eventId));
}

final fieldEventsRepositoryProvider =
    Provider<FieldEventsRepository>((ref) => FieldEventsRepository(ref.watch(apiClientProvider)));

typedef _FieldEventsQuery = ({String sport, String status});

final fieldEventsListProvider = FutureProvider.family<List<FieldEvent>, _FieldEventsQuery>((ref, query) {
  final repository = ref.watch(fieldEventsRepositoryProvider);
  return lastKnownThenFresh(
    ref,
    key: 'events/${query.sport}/${query.status}',
    fetch: () => repository.fetchEvents(query.sport, status: query.status),
    parse: FieldEventsRepository.parseEvents,
  );
});

typedef _FieldEventQuery = ({String sport, String eventId});

final fieldChildEventsProvider = FutureProvider.family<List<FieldEvent>, _FieldEventQuery>((ref, query) {
  return ref.watch(fieldEventsRepositoryProvider).listChildEvents(query.sport, query.eventId);
});

final fieldEventPredictionProvider = FutureProvider.family<PgaEventPrediction, _FieldEventQuery>((ref, query) {
  final repository = ref.watch(fieldEventsRepositoryProvider);
  return lastKnownThenFresh(
    ref,
    key: 'prediction/${query.sport}/${query.eventId}',
    fetch: () => repository.fetchEventPrediction(query.sport, query.eventId),
    parse: FieldEventsRepository.parsePrediction,
  );
});

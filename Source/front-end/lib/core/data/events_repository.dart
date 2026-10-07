import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/api_client.dart';
import '../api/api_routes.dart';
import '../models/event.dart';
import '../models/event_status.dart';
import '../models/prediction.dart';
import 'last_known_cache.dart';

/// Thrown by getEventPrediction on a cold cache miss (predict-read's own
/// 202 -- see Source/aws-lambdas/nfl/predict-read/handler.py's module
/// docstring): the compute was just triggered asynchronously, nothing to
/// show yet. Carries the backend's own suggested wait so callers don't
/// have to hardcode it.
class PredictionComputingException implements Exception {
  const PredictionComputingException(this.retryAfterSeconds);
  final int retryAfterSeconds;
}

/// Generic, sport-parametrized: every sport hits the same route shapes
/// (see core/models/sport_config.dart's own doc comment).
class EventsRepository {
  EventsRepository(this._api);

  final ApiClient _api;

  /// The raw GET /{sport}/events body -- see [parseEvents].
  Future<Object?> fetchEvents(String sport, {String status = EventStatus.scheduled}) =>
      _api.get(ApiRoutes.events(sport), queryParameters: {'status': status});

  static List<SportEvent> parseEvents(Object? json) {
    final events = (json as Map<String, dynamic>)['events'] as List<dynamic>? ?? [];
    return events.map((e) => SportEvent.fromJson(e as Map<String, dynamic>)).toList();
  }

  Future<List<SportEvent>> listEvents(String sport, {String status = EventStatus.scheduled}) async =>
      parseEvents(await fetchEvents(sport, status: status));

  Future<EventPrediction> getEventPrediction(String sport, String eventId) async {
    final response = await _api.get(ApiRoutes.eventPrediction(sport, eventId)) as Map<String, dynamic>;
    // ApiClient._decode treats 202 the same as any other 2xx -- the
    // "computing" shape is the only way to tell the two apart, since the
    // HTTP status code itself isn't threaded through this far.
    if (response['status'] == 'computing') {
      throw PredictionComputingException(response['retry_after_seconds'] as int? ?? 5);
    }
    return EventPrediction.fromJson(response);
  }
}

/// Predictions that arrived inside a scheduled events list, waiting for
/// [eventPredictionProvider] to pick them up in place of its own request.
/// Each is handed out once: a later refresh of that provider goes to the
/// prediction route, which is what starts a recompute for a stale one.
class PredictionSeeds {
  final Map<String, EventPrediction> _seeds = {};

  static String _key(String sport, String eventId) => '$sport/$eventId';

  void put(String sport, String eventId, EventPrediction prediction) => _seeds[_key(sport, eventId)] = prediction;

  EventPrediction? take(String sport, String eventId) => _seeds.remove(_key(sport, eventId));
}

final predictionSeedsProvider = Provider<PredictionSeeds>((ref) => PredictionSeeds());

final eventsRepositoryProvider = Provider<EventsRepository>((ref) => EventsRepository(ref.watch(apiClientProvider)));

typedef _EventsQuery = ({String sport, String status});

final eventsListProvider = FutureProvider.family<List<SportEvent>, _EventsQuery>((ref, query) async {
  final repository = ref.watch(eventsRepositoryProvider);
  final events = await lastKnownThenFresh(
    ref,
    key: 'events/${query.sport}/${query.status}',
    fetch: () => repository.fetchEvents(query.sport, status: query.status),
    parse: EventsRepository.parseEvents,
  );
  // Hands each embedded prediction to its own provider, replacing whatever
  // an earlier list (or the copy saved on this device) had put there.
  final seeds = ref.read(predictionSeedsProvider);
  for (final event in events) {
    final prediction = event.prediction;
    if (prediction == null) continue;
    seeds.put(query.sport, event.eventId, prediction);
    ref.invalidate(eventPredictionProvider((sport: query.sport, eventId: event.eventId)));
  }
  return events;
});

typedef _EventQuery = ({String sport, String eventId});

final eventPredictionProvider = FutureProvider.family<EventPrediction, _EventQuery>((ref, query) {
  final seeded = ref.read(predictionSeedsProvider).take(query.sport, query.eventId);
  if (seeded != null) return seeded;
  return ref.watch(eventsRepositoryProvider).getEventPrediction(query.sport, query.eventId);
});

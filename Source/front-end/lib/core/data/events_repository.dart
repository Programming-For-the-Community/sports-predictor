import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/api_client.dart';
import '../models/event.dart';
import '../models/prediction.dart';

/// Thrown by getEventPrediction on a cold cache miss (predict-read's own
/// 202 -- see Source/aws-lambdas/nfl/predict-read/handler.py's module
/// docstring): the compute was just triggered asynchronously, nothing to
/// show yet. Carries the backend's own suggested wait so callers don't
/// have to hardcode it.
class PredictionComputingException implements Exception {
  const PredictionComputingException(this.retryAfterSeconds);
  final int retryAfterSeconds;
}

/// Generic, sport-parametrized -- not one repository class per sport,
/// since every sport hits the same route shapes by backend design (see
/// core/models/sport_config.dart's own doc comment).
class EventsRepository {
  EventsRepository(this._api);

  final ApiClient _api;

  Future<List<SportEvent>> listEvents(String sport, {String status = 'scheduled'}) async {
    final response = await _api.get('/$sport/events', queryParameters: {'status': status}) as Map<String, dynamic>;
    final events = response['events'] as List<dynamic>? ?? [];
    return events.map((e) => SportEvent.fromJson(e as Map<String, dynamic>)).toList();
  }

  Future<EventPrediction> getEventPrediction(String sport, String eventId) async {
    final response = await _api.get('/$sport/predictions/events/$eventId') as Map<String, dynamic>;
    // ApiClient._decode treats 202 the same as any other 2xx -- the
    // "computing" shape is the only way to tell the two apart, since the
    // HTTP status code itself isn't threaded through this far.
    if (response['status'] == 'computing') {
      throw PredictionComputingException(response['retry_after_seconds'] as int? ?? 5);
    }
    return EventPrediction.fromJson(response);
  }
}

final eventsRepositoryProvider = Provider<EventsRepository>((ref) => EventsRepository(ref.watch(apiClientProvider)));

typedef _EventsQuery = ({String sport, String status});

final eventsListProvider = FutureProvider.family<List<SportEvent>, _EventsQuery>((ref, query) {
  return ref.watch(eventsRepositoryProvider).listEvents(query.sport, status: query.status);
});

typedef _EventQuery = ({String sport, String eventId});

final eventPredictionProvider = FutureProvider.family<EventPrediction, _EventQuery>((ref, query) {
  return ref.watch(eventsRepositoryProvider).getEventPrediction(query.sport, query.eventId);
});

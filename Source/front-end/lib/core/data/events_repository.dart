import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/api_client.dart';
import '../models/event.dart';
import '../models/prediction.dart';

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

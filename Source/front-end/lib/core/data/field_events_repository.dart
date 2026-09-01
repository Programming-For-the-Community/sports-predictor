import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/api_client.dart';
import '../models/event_status.dart';
import '../models/field_event.dart';
import '../models/field_prediction.dart';
import 'events_repository.dart' show PredictionComputingException;

export 'events_repository.dart' show PredictionComputingException;

/// PGA's own repository, parallel to EventsRepository (events_repository.dart)
/// rather than folded into it -- PGA's /events and /predictions responses
/// are genuinely different shapes (field_event.dart/field_prediction.dart),
/// not variants of SportEvent/EventPrediction.
class FieldEventsRepository {
  FieldEventsRepository(this._api);

  final ApiClient _api;

  Future<List<FieldEvent>> listEvents(String sport, {String status = EventStatus.scheduled}) async {
    final response = await _api.get('/$sport/events', queryParameters: {'status': status}) as Map<String, dynamic>;
    final events = response['events'] as List<dynamic>? ?? [];
    return events.map((e) => FieldEvent.fromJson(e as Map<String, dynamic>)).toList();
  }

  Future<PgaEventPrediction> getEventPrediction(String sport, String eventId) async {
    final response = await _api.get('/$sport/predictions/events/$eventId') as Map<String, dynamic>;
    // Same "computing" cache-miss shape as every other sport's predict
    // route -- reuses EventsRepository's own exception type rather than
    // duplicating it, since callers already catch this type generically.
    if (response['status'] == 'computing') {
      throw PredictionComputingException(response['retry_after_seconds'] as int? ?? 5);
    }
    return parsePgaEventPrediction(response);
  }
}

final fieldEventsRepositoryProvider =
    Provider<FieldEventsRepository>((ref) => FieldEventsRepository(ref.watch(apiClientProvider)));

typedef _FieldEventsQuery = ({String sport, String status});

final fieldEventsListProvider = FutureProvider.family<List<FieldEvent>, _FieldEventsQuery>((ref, query) {
  return ref.watch(fieldEventsRepositoryProvider).listEvents(query.sport, status: query.status);
});

typedef _FieldEventQuery = ({String sport, String eventId});

final fieldEventPredictionProvider = FutureProvider.family<PgaEventPrediction, _FieldEventQuery>((ref, query) {
  return ref.watch(fieldEventsRepositoryProvider).getEventPrediction(query.sport, query.eventId);
});

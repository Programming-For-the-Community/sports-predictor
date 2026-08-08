import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/api_client.dart';
import '../models/live_score.dart';

/// Generic, sport-parametrized -- same convention as EventsRepository.
class LiveScoresRepository {
  LiveScoresRepository(this._api);

  final ApiClient _api;

  Future<Map<String, LiveEventState>> getLiveScores(String sport) async {
    final response = await _api.get('/$sport/live-scores') as Map<String, dynamic>;
    final events = response['events'] as Map<String, dynamic>? ?? {};
    return events.map((eventId, state) => MapEntry(eventId, LiveEventState.fromJson(state as Map<String, dynamic>)));
  }
}

final liveScoresRepositoryProvider =
    Provider<LiveScoresRepository>((ref) => LiveScoresRepository(ref.watch(apiClientProvider)));

final liveScoresProvider = FutureProvider.family<Map<String, LiveEventState>, String>((ref, sport) {
  return ref.watch(liveScoresRepositoryProvider).getLiveScores(sport);
});

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/api_client.dart';
import '../models/field_live_score.dart';
import '../models/live_score.dart';

/// Generic, sport-parametrized repository over GET /{sport}/live-scores.
/// Three value-shape parses over the same route -- getLiveScores for
/// every head-to-head sport's home/away shape, getPgaLiveScores for PGA's
/// own discriminated field/match_play/cup shape (each cache entry carries
/// its own event_type -- see field_live_score.dart's own docstring), and
/// getFieldLiveScores (filtered down to just the field-typed entries, for
/// callers -- FieldLeaderboardTable's own liveResults param -- that only
/// ever render a field event and shouldn't have to pattern-match the
/// union themselves). Same "one class, multiple concerns" split
/// EventsRepository already uses for list/predict.
class LiveScoresRepository {
  LiveScoresRepository(this._api);

  final ApiClient _api;

  Future<Map<String, LiveEventState>> getLiveScores(String sport) async {
    final response = await _api.get('/$sport/live-scores') as Map<String, dynamic>;
    final events = response['events'] as Map<String, dynamic>? ?? {};
    return events.map((eventId, state) => MapEntry(eventId, LiveEventState.fromJson(state as Map<String, dynamic>)));
  }

  Future<Map<String, PgaLiveEventState>> getPgaLiveScores(String sport) async {
    final response = await _api.get('/$sport/live-scores') as Map<String, dynamic>;
    final events = response['events'] as Map<String, dynamic>? ?? {};
    return events.map((eventId, state) => MapEntry(eventId, parsePgaLiveEventState(state as Map<String, dynamic>)));
  }

  Future<Map<String, FieldLiveEventState>> getFieldLiveScores(String sport) async {
    final all = await getPgaLiveScores(sport);
    return {
      for (final entry in all.entries)
        if (entry.value case PgaFieldLiveState(:final state)) entry.key: state,
    };
  }
}

final liveScoresRepositoryProvider =
    Provider<LiveScoresRepository>((ref) => LiveScoresRepository(ref.watch(apiClientProvider)));

final liveScoresProvider = FutureProvider.family<Map<String, LiveEventState>, String>((ref, sport) {
  return ref.watch(liveScoresRepositoryProvider).getLiveScores(sport);
});

final pgaLiveScoresProvider = FutureProvider.family<Map<String, PgaLiveEventState>, String>((ref, sport) {
  return ref.watch(liveScoresRepositoryProvider).getPgaLiveScores(sport);
});

final fieldLiveScoresProvider = FutureProvider.family<Map<String, FieldLiveEventState>, String>((ref, sport) {
  return ref.watch(liveScoresRepositoryProvider).getFieldLiveScores(sport);
});

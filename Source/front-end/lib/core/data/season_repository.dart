import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/api_client.dart';
import '../models/season_projection.dart';

class SeasonRepository {
  SeasonRepository(this._api);

  final ApiClient _api;

  Future<SeasonProjection> getSeasonProjection(String sport) async {
    final response = await _api.get('/$sport/season') as Map<String, dynamic>;
    return SeasonProjection.fromJson(response);
  }
}

final seasonRepositoryProvider = Provider<SeasonRepository>((ref) => SeasonRepository(ref.watch(apiClientProvider)));

final seasonProjectionProvider = FutureProvider.family<SeasonProjection, String>((ref, sport) {
  return ref.watch(seasonRepositoryProvider).getSeasonProjection(sport);
});

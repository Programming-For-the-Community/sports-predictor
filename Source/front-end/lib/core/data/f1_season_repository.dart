import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/api_client.dart';
import '../models/f1_season_projection.dart';
import '../models/sport_config.dart';

/// F1's own /f1/season repository -- same trivial GET+FutureProvider
/// shape as pga_season_repository.dart. Not folded into that one, or into
/// SeasonRepository -- the response shape (driver AND constructor
/// standings from one simulated pass, no Playoffs-field probabilities) is
/// genuinely different from both, see f1_season_projection.dart's own
/// docstring.
class F1SeasonRepository {
  F1SeasonRepository(this._api);

  final ApiClient _api;

  Future<F1SeasonProjection> getSeasonProjection() async {
    final response = await _api.get('/${SportIds.f1}/season') as Map<String, dynamic>;
    return F1SeasonProjection.fromJson(response);
  }
}

final f1SeasonRepositoryProvider = Provider<F1SeasonRepository>(
  (ref) => F1SeasonRepository(ref.watch(apiClientProvider)),
);

final f1SeasonProjectionProvider = FutureProvider<F1SeasonProjection>((ref) {
  return ref.watch(f1SeasonRepositoryProvider).getSeasonProjection();
});

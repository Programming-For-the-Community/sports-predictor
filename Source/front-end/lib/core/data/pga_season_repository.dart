import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/api_client.dart';
import '../models/pga_season_projection.dart';
import '../models/sport_config.dart';

/// PGA's own /pga/season repository -- same trivial GET+FutureProvider
/// shape as season_repository.dart, a real precedent for a sport-specific
/// sibling repo already exists (field_events_repository.dart next to
/// events_repository.dart). Not folded into SeasonRepository -- the
/// response shape (a points race, no bracket/division) is genuinely
/// different, see pga_season_projection.dart's own docstring.
class PgaSeasonRepository {
  PgaSeasonRepository(this._api);

  final ApiClient _api;

  Future<PgaSeasonProjection> getSeasonProjection() async {
    final response = await _api.get('/${SportIds.pga}/season') as Map<String, dynamic>;
    return PgaSeasonProjection.fromJson(response);
  }
}

final pgaSeasonRepositoryProvider = Provider<PgaSeasonRepository>(
  (ref) => PgaSeasonRepository(ref.watch(apiClientProvider)),
);

final pgaSeasonProjectionProvider = FutureProvider<PgaSeasonProjection>((ref) {
  return ref.watch(pgaSeasonRepositoryProvider).getSeasonProjection();
});

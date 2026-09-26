import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/api_client.dart';
import '../api/api_routes.dart';
import '../models/model_performance.dart';

class ModelPerformanceRepository {
  ModelPerformanceRepository(this._api);

  final ApiClient _api;

  Future<ModelPerformance> getModelPerformance(String sport) async {
    final response = await _api.get(ApiRoutes.modelPerformance(sport)) as Map<String, dynamic>;
    return ModelPerformance.fromJson(response);
  }
}

final modelPerformanceRepositoryProvider =
    Provider<ModelPerformanceRepository>((ref) => ModelPerformanceRepository(ref.watch(apiClientProvider)));

final modelPerformanceProvider = FutureProvider.family<ModelPerformance, String>((ref, sport) {
  return ref.watch(modelPerformanceRepositoryProvider).getModelPerformance(sport);
});

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/api_client.dart';
import '../models/model_card.dart';

class ModelsRepository {
  ModelsRepository(this._api);

  final ApiClient _api;

  Future<List<ModelCard>> listModels(String sport) async {
    final response = await _api.get('/$sport/models') as Map<String, dynamic>;
    final models = response['models'] as List<dynamic>? ?? [];
    return models.map((m) => ModelCard.fromJson(m as Map<String, dynamic>)).toList();
  }
}

final modelsRepositoryProvider = Provider<ModelsRepository>((ref) => ModelsRepository(ref.watch(apiClientProvider)));

final modelsListProvider = FutureProvider.family<List<ModelCard>, String>((ref, sport) {
  return ref.watch(modelsRepositoryProvider).listModels(sport);
});

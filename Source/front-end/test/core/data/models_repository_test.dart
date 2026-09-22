import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

import 'package:front_end/core/data/models_repository.dart';

import '../../support/api_client_test_support.dart';

Map<String, dynamic> _model({String name = 'nfl-win-probability'}) => {
      'model_name': name,
      'algorithm': 'xgboost',
      'version': 3,
      'trained_at': '2026-09-01T00:00:00Z',
    };

void main() {
  test('requests the sport-scoped models route', () async {
    Uri? capturedUri;
    final repo = ModelsRepository(buildTestApiClient((request) async {
      capturedUri = request.url;
      return http.Response(jsonEncode({'models': []}), 200);
    }));

    await repo.listModels('nfl');

    expect(capturedUri?.path, '/nfl/models');
  });

  test('parses every model in the response', () async {
    final repo = ModelsRepository(buildTestApiClient((request) async {
      return http.Response(jsonEncode({'models': [_model(name: 'a'), _model(name: 'b')]}), 200);
    }));

    final models = await repo.listModels('nfl');

    expect(models.map((m) => m.modelName), ['a', 'b']);
  });

  test('defaults to an empty list when the models key is missing', () async {
    final repo = ModelsRepository(buildTestApiClient((request) async => http.Response('{}', 200)));

    expect(await repo.listModels('nfl'), isEmpty);
  });

  test('modelsListProvider resolves through modelsRepositoryProvider for the given sport', () async {
    final container = buildTestContainer((request) async {
      return http.Response(jsonEncode({'models': [_model(name: 'a')]}), 200);
    });
    addTearDown(container.dispose);

    final models = await container.read(modelsListProvider('nfl').future);

    expect(models.map((m) => m.modelName), ['a']);
  });
}

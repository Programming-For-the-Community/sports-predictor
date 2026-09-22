import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

import 'package:front_end/core/data/f1_season_repository.dart';

import '../../support/api_client_test_support.dart';

void main() {
  test('requests the fixed F1 season route', () async {
    Uri? capturedUri;
    final repo = F1SeasonRepository(buildTestApiClient((request) async {
      capturedUri = request.url;
      return http.Response(jsonEncode({'season': 2026, 'driver_standings': [], 'constructor_standings': []}), 200);
    }));

    await repo.getSeasonProjection();

    expect(capturedUri?.path, '/f1/season');
  });

  test('parses both driver and constructor standings from the response', () async {
    final repo = F1SeasonRepository(buildTestApiClient((request) async {
      return http.Response(
        jsonEncode({
          'season': 2026,
          'driver_standings': [
            {'entity_id': 'ver', 'current_points': 350.0, 'projected_points': 420.0, 'champion_probability': 0.8},
          ],
          'constructor_standings': [
            {'entity_id': 'red_bull', 'current_points': 600.0, 'projected_points': 700.0, 'champion_probability': 0.85},
          ],
        }),
        200,
      );
    }));

    final projection = await repo.getSeasonProjection();

    expect(projection.driverStandings.single.entityId, 'ver');
    expect(projection.constructorStandings.single.entityId, 'red_bull');
  });

  test('f1SeasonProjectionProvider resolves through f1SeasonRepositoryProvider', () async {
    final container = buildTestContainer((request) async {
      return http.Response(jsonEncode({'season': 2026, 'driver_standings': [], 'constructor_standings': []}), 200);
    });
    addTearDown(container.dispose);

    final projection = await container.read(f1SeasonProjectionProvider.future);

    expect(projection.season, 2026);
  });
}

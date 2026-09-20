import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

import 'package:front_end/core/data/pga_season_repository.dart';

import '../../support/api_client_test_support.dart';

void main() {
  test('requests the fixed PGA season route', () async {
    Uri? capturedUri;
    final repo = PgaSeasonRepository(buildTestApiClient((request) async {
      capturedUri = request.url;
      return http.Response(jsonEncode({'season': 2026, 'standings': []}), 200);
    }));

    await repo.getSeasonProjection();

    expect(capturedUri?.path, '/pga/season');
  });

  test('parses the FedEx Cup standings response', () async {
    final repo = PgaSeasonRepository(buildTestApiClient((request) async {
      return http.Response(
        jsonEncode({
          'season': 2026,
          'standings': [
            {
              'entity_id': 'scheffler',
              'current_points': 2500.0,
              'projected_points': 3000.0,
              'fedex_st_jude_probability': 0.99,
              'bmw_probability': 0.97,
              'tour_championship_probability': 0.9,
              'champion_probability': 0.25,
            },
          ],
        }),
        200,
      );
    }));

    final projection = await repo.getSeasonProjection();

    expect(projection.season, 2026);
    expect(projection.standings.single.entityId, 'scheffler');
  });
}

import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

import 'package:front_end/core/data/season_repository.dart';

import '../../support/api_client_test_support.dart';

void main() {
  test('requests the sport-scoped season route', () async {
    Uri? capturedUri;
    final repo = SeasonRepository(buildTestApiClient((request) async {
      capturedUri = request.url;
      return http.Response(jsonEncode({'sport': 'nfl', 'standings': []}), 200);
    }));

    await repo.getSeasonProjection('nfl');

    expect(capturedUri?.path, '/nfl/season');
  });

  test('parses the season projection response', () async {
    final repo = SeasonRepository(buildTestApiClient((request) async {
      return http.Response(
        jsonEncode({
          'sport': 'nfl',
          'season': 2025,
          'standings': [
            {'team_id': 'KC', 'wins': 10, 'losses': 3},
          ],
        }),
        200,
      );
    }));

    final projection = await repo.getSeasonProjection('nfl');

    expect(projection.sport, 'nfl');
    expect(projection.standings.single.teamId, 'KC');
  });
}

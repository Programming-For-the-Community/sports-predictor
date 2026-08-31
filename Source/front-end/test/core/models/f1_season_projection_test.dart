import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/models/f1_season_projection.dart';

void main() {
  test('parses a full championship season projection response', () {
    final season = F1SeasonProjection.fromJson({
      'sport': 'f1', 'season': 2026,
      'driver_standings': [
        {'entity_id': 'max_verstappen', 'name': 'Max Verstappen', 'current_points': 350.0, 'projected_points': 410.5, 'champion_probability': 0.62},
      ],
      'constructor_standings': [
        {'entity_id': 'red_bull', 'name': 'Red Bull', 'current_points': 600.0, 'projected_points': 700.0, 'champion_probability': 0.7},
      ],
      'simulations': 750,
      'generated_at': '2026-08-28T00:00:00Z',
    });

    expect(season.season, 2026);
    expect(season.simulations, 750);
    final driver = season.driverStandings.single;
    expect(driver.entityId, 'max_verstappen');
    expect(driver.currentPoints, 350.0);
    expect(driver.projectedPoints, 410.5);
    expect(driver.championProbability, 0.62);

    final constructor = season.constructorStandings.single;
    expect(constructor.entityId, 'red_bull');
    expect(constructor.championProbability, 0.7);
  });

  test('empty standings lists parse cleanly rather than throwing', () {
    final season = F1SeasonProjection.fromJson({'season': 2026, 'driver_standings': [], 'constructor_standings': [], 'simulations': 0});

    expect(season.driverStandings, isEmpty);
    expect(season.constructorStandings, isEmpty);
  });

  test('simulations defaults to 0 when omitted (real final standings, nothing simulated)', () {
    final season = F1SeasonProjection.fromJson({'season': 2026, 'driver_standings': [], 'constructor_standings': []});
    expect(season.simulations, 0);
  });

  test('name is null when the entity lookup found nothing', () {
    final season = F1SeasonProjection.fromJson({
      'season': 2026,
      'driver_standings': [
        {'entity_id': '999', 'current_points': 0.0, 'projected_points': 0.0, 'champion_probability': 0.0},
      ],
      'constructor_standings': [],
    });

    expect(season.driverStandings.single.name, isNull);
  });
}

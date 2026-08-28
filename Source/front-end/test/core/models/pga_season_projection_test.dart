import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/models/pga_season_projection.dart';

void main() {
  test('parses a full FedEx Cup season projection response', () {
    final season = PgaSeasonProjection.fromJson({
      'sport': 'pga',
      'season': 2026,
      'standings': [
        {
          'entity_id': '10140',
          'name': 'Scottie Scheffler',
          'country': 'USA',
          'current_points': 5200.0,
          'projected_points': 6100.5,
          'fedex_st_jude_probability': 0.98,
          'bmw_probability': 0.95,
          'tour_championship_probability': 0.9,
          'champion_probability': 0.31,
        },
      ],
      'simulations': 750,
      'generated_at': '2026-08-28T00:00:00Z',
    });

    expect(season.season, 2026);
    expect(season.simulations, 750);
    final row = season.standings.single;
    expect(row.entityId, '10140');
    expect(row.name, 'Scottie Scheffler');
    expect(row.country, 'USA');
    expect(row.currentPoints, 5200.0);
    expect(row.projectedPoints, 6100.5);
    expect(row.fedexStJudeProbability, 0.98);
    expect(row.bmwProbability, 0.95);
    expect(row.tourChampionshipProbability, 0.9);
    expect(row.championProbability, 0.31);
  });

  test('an empty standings list parses cleanly rather than throwing', () {
    final season = PgaSeasonProjection.fromJson({'season': 2026, 'standings': [], 'simulations': 0});

    expect(season.standings, isEmpty);
    expect(season.simulations, 0);
  });

  test('simulations defaults to 0 when omitted (real final standings, nothing simulated)', () {
    final season = PgaSeasonProjection.fromJson({'season': 2026, 'standings': []});

    expect(season.simulations, 0);
  });

  test('name and country are null when the entity lookup found nothing', () {
    final season = PgaSeasonProjection.fromJson({
      'season': 2026,
      'standings': [
        {
          'entity_id': '999', 'current_points': 0.0, 'projected_points': 0.0,
          'fedex_st_jude_probability': 0.0, 'bmw_probability': 0.0,
          'tour_championship_probability': 0.0, 'champion_probability': 0.0,
        },
      ],
    });

    final row = season.standings.single;
    expect(row.name, isNull);
    expect(row.country, isNull);
  });
}

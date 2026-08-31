/// Mirrors GET /f1/season's response shape -- aws-lambdas/f1/predict/
/// season_projection.py's build_season_projection. Genuinely different
/// from PgaSeasonProjection (pga_season_projection.dart) in the one way
/// F1 has no PGA analog for at all: TWO standings lists from the same
/// simulated pass, driver AND constructor -- no Playoffs-field
/// probabilities either, F1's field is fixed all season (see that
/// module's own docstring).
library;

class F1DriverStanding {
  const F1DriverStanding({
    required this.entityId,
    required this.currentPoints,
    required this.projectedPoints,
    required this.championProbability,
    this.name,
  });

  final String entityId;
  final String? name;
  final double currentPoints;
  // Monte Carlo mean across every simulated remaining race, on top of
  // currentPoints -- equal to currentPoints once the season is over.
  final double projectedPoints;
  final double championProbability;

  factory F1DriverStanding.fromJson(Map<String, dynamic> json) => F1DriverStanding(
        entityId: json['entity_id'] as String,
        name: json['name'] as String?,
        currentPoints: (json['current_points'] as num).toDouble(),
        projectedPoints: (json['projected_points'] as num).toDouble(),
        championProbability: (json['champion_probability'] as num).toDouble(),
      );
}

class F1ConstructorStanding {
  const F1ConstructorStanding({
    required this.entityId,
    required this.currentPoints,
    required this.projectedPoints,
    required this.championProbability,
    this.name,
  });

  final String entityId;
  final String? name;
  final double currentPoints;
  final double projectedPoints;
  final double championProbability;

  factory F1ConstructorStanding.fromJson(Map<String, dynamic> json) => F1ConstructorStanding(
        entityId: json['entity_id'] as String,
        name: json['name'] as String?,
        currentPoints: (json['current_points'] as num).toDouble(),
        projectedPoints: (json['projected_points'] as num).toDouble(),
        championProbability: (json['champion_probability'] as num).toDouble(),
      );
}

class F1SeasonProjection {
  const F1SeasonProjection({
    required this.season, required this.driverStandings, required this.constructorStandings, required this.simulations,
  });

  final int season;
  final List<F1DriverStanding> driverStandings;
  final List<F1ConstructorStanding> constructorStandings;
  // 0 once the season is fully over (real final standings, nothing left
  // to simulate) -- f1_season_page.dart uses this the same way
  // pga_season_page.dart does, to skip Monte-Carlo-flavored copy.
  final int simulations;

  factory F1SeasonProjection.fromJson(Map<String, dynamic> json) => F1SeasonProjection(
        season: json['season'] as int,
        driverStandings: (json['driver_standings'] as List<dynamic>? ?? [])
            .map((row) => F1DriverStanding.fromJson(row as Map<String, dynamic>))
            .toList(),
        constructorStandings: (json['constructor_standings'] as List<dynamic>? ?? [])
            .map((row) => F1ConstructorStanding.fromJson(row as Map<String, dynamic>))
            .toList(),
        simulations: json['simulations'] as int? ?? 0,
      );
}

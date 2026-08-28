/// Mirrors GET /pga/season's response shape -- aws-lambdas/pga/predict/
/// season_projection.py's build_season_projection. A points-standings
/// race, no bracket, no division/conference grouping -- a different
/// shape from season_projection.dart's TeamStanding/SeasonProjection.
class PgaFedexStanding {
  const PgaFedexStanding({
    required this.entityId,
    required this.currentPoints,
    required this.projectedPoints,
    required this.fedexStJudeProbability,
    required this.bmwProbability,
    required this.tourChampionshipProbability,
    required this.championProbability,
    this.name,
    this.country,
  });

  final String entityId;
  final String? name;
  final String? country;
  // Real points already earned this season.
  final double currentPoints;
  // Monte Carlo mean across every simulated remaining event, on top of
  // currentPoints -- equal to currentPoints once the season is over.
  final double projectedPoints;
  // Probability of finishing the points race inside the top 70 -> the
  // FedEx St. Jude Championship field.
  final double fedexStJudeProbability;
  // Top 50 -> the BMW Championship field.
  final double bmwProbability;
  // Top 30 -> the TOUR Championship field. Under the 2025+ format it
  // awards no further points -- this is purely "made the season finale."
  final double tourChampionshipProbability;
  // Probability of winning TOUR Championship outright -- the sole
  // determinant of the FedEx Cup Champion under the 2025+ format.
  final double championProbability;

  factory PgaFedexStanding.fromJson(Map<String, dynamic> json) => PgaFedexStanding(
        entityId: json['entity_id'] as String,
        name: json['name'] as String?,
        country: json['country'] as String?,
        currentPoints: (json['current_points'] as num).toDouble(),
        projectedPoints: (json['projected_points'] as num).toDouble(),
        fedexStJudeProbability: (json['fedex_st_jude_probability'] as num).toDouble(),
        bmwProbability: (json['bmw_probability'] as num).toDouble(),
        tourChampionshipProbability: (json['tour_championship_probability'] as num).toDouble(),
        championProbability: (json['champion_probability'] as num).toDouble(),
      );
}

class PgaSeasonProjection {
  const PgaSeasonProjection({required this.season, required this.standings, required this.simulations});

  final int season;
  final List<PgaFedexStanding> standings;
  // 0 once the season is fully over (real final standings, nothing left
  // to simulate) -- pgaSeasonPage.dart uses this to skip Monte-Carlo-
  // flavored copy ("simulated N times") when the numbers are real.
  final int simulations;

  factory PgaSeasonProjection.fromJson(Map<String, dynamic> json) => PgaSeasonProjection(
        season: json['season'] as int,
        standings: (json['standings'] as List<dynamic>? ?? [])
            .map((row) => PgaFedexStanding.fromJson(row as Map<String, dynamic>))
            .toList(),
        simulations: json['simulations'] as int? ?? 0,
      );
}

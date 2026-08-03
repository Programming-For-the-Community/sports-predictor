/// Mirrors GET /{sport}/season's response shape (see
/// Source/aws-lambdas/nfl/predict/handler.py's _season_projection).
class TeamStanding {
  const TeamStanding({
    required this.teamId,
    required this.wins,
    required this.losses,
    required this.projectedWins,
    required this.divisionWinnerProbability,
    required this.playoffProbability,
    required this.championshipProbability,
  });

  final String teamId;
  final int wins;
  final int losses;
  final double projectedWins;
  final double divisionWinnerProbability;
  final double playoffProbability;
  final double championshipProbability;

  factory TeamStanding.fromJson(Map<String, dynamic> json) => TeamStanding(
        teamId: json['team_id'] as String,
        wins: json['wins'] as int,
        losses: json['losses'] as int,
        projectedWins: (json['projected_wins'] as num).toDouble(),
        divisionWinnerProbability: (json['division_winner_probability'] as num).toDouble(),
        playoffProbability: (json['playoff_probability'] as num).toDouble(),
        championshipProbability: (json['championship_probability'] as num).toDouble(),
      );
}

/// One row in a player-prop leaderboard -- `name` falls back to
/// `entityId` the same way PlayerStatLine does (see event_leaders.dart).
class LeaderboardEntry {
  const LeaderboardEntry({
    required this.entityId,
    required this.name,
    required this.currentTotal,
    required this.projectedTotal,
  });

  final String entityId;
  final String? name;
  final double currentTotal;
  final double projectedTotal;

  String get displayName => name ?? entityId;

  factory LeaderboardEntry.fromJson(Map<String, dynamic> json) => LeaderboardEntry(
        entityId: json['entity_id'] as String,
        name: json['name'] as String?,
        currentTotal: (json['current_total'] as num).toDouble(),
        projectedTotal: (json['projected_total'] as num).toDouble(),
      );
}

class SeasonProjection {
  const SeasonProjection({
    required this.sport,
    required this.season,
    required this.standings,
    required this.leaderboards,
  });

  final String sport;
  final int? season;

  /// Already sorted by projected_wins descending server-side.
  final List<TeamStanding> standings;

  /// Keyed by TARGET_STAT (e.g. "passing_yards") -- see handler.py's
  /// PLAYER_PROP_STATS for the full list. Null if the backend couldn't
  /// compute leaderboards (best-effort field, same as EventLeaders).
  final Map<String, List<LeaderboardEntry>>? leaderboards;

  factory SeasonProjection.fromJson(Map<String, dynamic> json) => SeasonProjection(
        sport: json['sport'] as String,
        season: json['season'] as int?,
        standings: (json['standings'] as List<dynamic>? ?? [])
            .map((s) => TeamStanding.fromJson(s as Map<String, dynamic>))
            .toList(),
        leaderboards: json['leaderboards'] != null
            ? (json['leaderboards'] as Map<String, dynamic>).map(
                (stat, entries) => MapEntry(
                  stat,
                  (entries as List<dynamic>)
                      .map((e) => LeaderboardEntry.fromJson(e as Map<String, dynamic>))
                      .toList(),
                ),
              )
            : null,
      );
}

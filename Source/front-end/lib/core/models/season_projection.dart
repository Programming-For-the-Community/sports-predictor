/// Mirrors GET /{sport}/season's response shape (see
/// Source/aws-lambdas/nfl/predict/handler.py's _season_projection).
class TeamStanding {
  const TeamStanding({
    required this.teamId,
    required this.division,
    required this.wins,
    required this.losses,
    required this.ties,
    required this.projectedWins,
    required this.projectedLosses,
    required this.divisionWinnerProbability,
    required this.playoffProbability,
    required this.championshipProbability,
  });

  final String teamId;
  // "AFC East"/"NFC West"/etc, for grouping standings by division (see
  // season_page.dart) -- null only for a non-franchise participant that
  // slipped past is_real_franchise_matchup somehow, not expected in
  // practice.
  final String? division;
  // Actual, this-season-so-far record.
  final int wins;
  final int losses;
  final int ties;
  // Monte Carlo season-end projection (season_simulation.simulate_season)
  // -- no projectedTies, that simulation draws a strict win/loss per game,
  // never a tie (see that function's own docstring).
  final double projectedWins;
  final double projectedLosses;
  final double divisionWinnerProbability;
  final double playoffProbability;
  final double championshipProbability;

  // ties/projected_losses default rather than require -- the season
  // projection this parses is a weekly-precomputed S3 payload
  // (scheduler-nfl-season-projection.tf), not computed fresh per
  // request, so a frontend deploy can land before that job has next run
  // with the fields that produce these two keys. Defaulting means a
  // brief "0" ties / "0" projected-losses window after a frontend
  // deploy instead of a hard parse failure -- self-heals the next time
  // the weekly job runs, no coordinated deploy required.
  factory TeamStanding.fromJson(Map<String, dynamic> json) => TeamStanding(
        teamId: json['team_id'] as String,
        division: json['division'] as String?,
        wins: json['wins'] as int,
        losses: json['losses'] as int,
        ties: json['ties'] as int? ?? 0,
        projectedWins: (json['projected_wins'] as num).toDouble(),
        projectedLosses: (json['projected_losses'] as num?)?.toDouble() ?? 0.0,
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

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
    this.abbreviation,
    this.currentRank,
  });

  final String teamId;
  // Off the team entity (see library.serving.common.enrich_team_standings)
  // -- same source and null-for-an-unseeded-entity caveat as Participant.
  // abbreviation. season_page.dart's _StandingsRow uses this via
  // teamDisplayFor, the same fallback rule teamDisplay applies to events.
  final String? abbreviation;
  // NCAAFB only -- today's actual (not simulated) National Ranking
  // position, 1-based (see aws-lambdas/ncaafb/predict/season_projection.
  // py's _current_rankings). Null for NFL (no such model/concept) and for
  // NCAAFB whenever the ranking model isn't promoted yet or fewer than
  // CFP_FIELD_SIZE teams are tracked -- same routine-not-error absence as
  // every other simulation-derived field on this class.
  final int? currentRank;
  // "AFC East"/"NFC West"/etc for NFL, or the team's conference ("SEC"/
  // "Big Ten"/etc, NCAAFB has no division concept) for every other sport
  // -- see fromJson below for which backend key each maps from. Used to
  // group standings (see season_page.dart's _groupByDivision) -- null
  // only for a non-franchise participant that slipped past
  // is_real_franchise_matchup (NFL) or an unresolved team_conference
  // entry (NCAAFB), not expected in practice.
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

  // Every simulation-derived field defaults rather than requires -- both
  // sports' build_season_projection merge `**simulation.get(team_id, {})`
  // into each row, and simulation itself is skipped entirely (see
  // aws-lambdas/ncaafb/predict/season_projection.py's own
  // build_season_projection) when fewer than CFP_FIELD_SIZE teams are
  // tracked yet or no ranking model has been promoted -- both routine
  // early-season states, not error states, so a standings row with none
  // of these fields is expected, not a parse failure. Same self-healing
  // reasoning already applied to ties/projected_losses now extends to
  // the rest: defaults now, real values once that week's job/model
  // catches up, no coordinated deploy required.
  //
  // division_winner_probability (NFL) and conference_champion_probability
  // (NCAAFB -- no division concept, see TeamStanding.division's own doc
  // comment) are the same "won their group" concept at each sport's own
  // granularity, so divisionWinnerProbability reads whichever key its
  // own sport's backend actually sends.
  factory TeamStanding.fromJson(Map<String, dynamic> json) => TeamStanding(
        teamId: json['team_id'] as String,
        division: json['division'] as String? ?? json['conference'] as String?,
        wins: json['wins'] as int,
        losses: json['losses'] as int,
        ties: json['ties'] as int? ?? 0,
        projectedWins: (json['projected_wins'] as num?)?.toDouble() ?? (json['wins'] as int).toDouble(),
        projectedLosses: (json['projected_losses'] as num?)?.toDouble() ?? 0.0,
        divisionWinnerProbability:
            (json['division_winner_probability'] as num? ?? json['conference_champion_probability'] as num?)
                    ?.toDouble() ??
                0.0,
        playoffProbability: (json['playoff_probability'] as num?)?.toDouble() ?? 0.0,
        championshipProbability: (json['championship_probability'] as num?)?.toDouble() ?? 0.0,
        abbreviation: json['abbreviation'] as String?,
        currentRank: json['current_rank'] as int?,
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

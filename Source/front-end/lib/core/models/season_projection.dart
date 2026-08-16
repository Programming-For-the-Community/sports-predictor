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
    this.playInProbability,
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
  // NBA only -- the fraction of simulated paths where this team finishes
  // seeds 7-10 and has to play at least one play-in game (see
  // aws-lambdas/nba/predict/season_simulation.py's own simulate_season
  // docstring for why this is a DIFFERENT stat from playoffProbability,
  // not a subset/superset of it). Null for every other sport -- no
  // play-in round exists outside the NBA.
  final double? playInProbability;

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
        playInProbability: (json['play_in_probability'] as num?)?.toDouble(),
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

/// One row in an NBA Cup group's standings -- see CupProjection's own doc
/// comment. `group` itself isn't carried on this row (already the key of
/// the map it lives in, see CupProjection.groups).
class CupTeamStanding {
  const CupTeamStanding({
    required this.teamId,
    required this.groupWins,
    required this.groupLosses,
    required this.groupWinnerProbability,
    required this.knockoutProbability,
    required this.cupFinalistProbability,
    required this.championProbability,
    this.name,
    this.abbreviation,
  });

  final String teamId;
  final String? name;
  final String? abbreviation;
  // Actual, this-Cup-so-far group-play record (NOT the team's overall
  // season record -- see aws-lambdas/nba/predict/season_projection.py's
  // own CUP_GROUP_PLAY_NOTE filtering).
  final int groupWins;
  final int groupLosses;
  final double groupWinnerProbability;
  final double knockoutProbability;
  final double cupFinalistProbability;
  final double championProbability;

  String get displayName => abbreviation ?? name ?? teamId;

  factory CupTeamStanding.fromJson(Map<String, dynamic> json) => CupTeamStanding(
        teamId: json['team_id'] as String,
        name: json['name'] as String?,
        abbreviation: json['abbreviation'] as String?,
        groupWins: json['group_wins'] as int? ?? 0,
        groupLosses: json['group_losses'] as int? ?? 0,
        groupWinnerProbability: (json['group_winner_probability'] as num?)?.toDouble() ?? 0.0,
        knockoutProbability: (json['knockout_probability'] as num?)?.toDouble() ?? 0.0,
        cupFinalistProbability: (json['cup_finalist_probability'] as num?)?.toDouble() ?? 0.0,
        championProbability: (json['champion_probability'] as num?)?.toDouble() ?? 0.0,
      );
}

/// NBA Cup (in-season tournament) projection -- a separate mid-season
/// competition from the end-of-year playoff odds already on
/// TeamStanding, see aws-lambdas/nba/predict/season_simulation.py's own
/// simulate_cup docstring. NBA only; every other sport's `cup` is null.
/// Null even for NBA whenever the current season's group assignments
/// haven't been added to library.features.nba_cup_groups.CUP_GROUPS yet
/// (season_projection.py's own best-effort field, same convention as
/// `leaderboards`) -- the season page should treat that exactly like
/// `leaderboards` being null: hide the section, not show an error.
class CupProjection {
  const CupProjection({required this.groups});

  /// "Eastern A"/"Western C"/etc -> that group's teams, already sorted
  /// server-side by real group_wins descending.
  final Map<String, List<CupTeamStanding>> groups;

  factory CupProjection.fromJson(Map<String, dynamic> json) => CupProjection(
        groups: (json['groups'] as Map<String, dynamic>).map(
          (group, teams) => MapEntry(
            group,
            (teams as List<dynamic>).map((t) => CupTeamStanding.fromJson(t as Map<String, dynamic>)).toList(),
          ),
        ),
      );
}

class SeasonProjection {
  const SeasonProjection({
    required this.sport,
    required this.season,
    required this.standings,
    required this.leaderboards,
    this.cup,
  });

  final String sport;
  final int? season;

  /// Already sorted by projected_wins descending server-side.
  final List<TeamStanding> standings;

  /// Keyed by TARGET_STAT (e.g. "passing_yards") -- see handler.py's
  /// PLAYER_PROP_STATS for the full list. Null if the backend couldn't
  /// compute leaderboards (best-effort field, same as EventLeaders).
  final Map<String, List<LeaderboardEntry>>? leaderboards;

  /// NBA only -- see CupProjection's own doc comment for the null cases.
  final CupProjection? cup;

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
        cup: json['cup'] != null ? CupProjection.fromJson(json['cup'] as Map<String, dynamic>) : null,
      );
}

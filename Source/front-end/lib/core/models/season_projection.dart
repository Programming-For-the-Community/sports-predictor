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

/// One resolved bracket slot -- the 3-state design from
/// aws-lambdas/nfl/predict/season_projection.py's own _resolve_matchup
/// docstring (mirrored identically in NCAAFB's/NBA's own
/// season_projection.py): "projected" (no real game exists yet -- the
/// model's own deterministic pick), "scheduled" (a real game exists, not
/// yet played -- its live prediction), or "final" (a real game exists
/// and is completed -- the actual result, plus whatever was originally
/// predicted for it if anyone ever requested one before it was played).
/// seedA/seedB are null on a cross-conference matchup (the Super Bowl/
/// NBA Finals/Cup Championship) -- the two sides' own conference seeds
/// aren't comparable on one shared scale.
class BracketMatchup {
  const BracketMatchup({
    required this.teamA,
    required this.teamB,
    required this.status,
    this.seedA,
    this.seedB,
    this.predictedWinner,
    this.winProbability,
    this.actualWinner,
    this.actualHomeScore,
    this.actualAwayScore,
  });

  final String teamA;
  final String teamB;
  final int? seedA;
  final int? seedB;
  final String status;

  /// Null only if a real, scheduled game exists but nobody's ever
  /// requested (or this run's own on-the-spot compute failed to produce)
  /// a prediction for it yet -- a real, if rare, transient gap, not a
  /// parse failure.
  final String? predictedWinner;
  final double? winProbability;

  /// Only present when status == "final".
  final String? actualWinner;
  final int? actualHomeScore;
  final int? actualAwayScore;

  bool get isFinal => status == 'final';

  factory BracketMatchup.fromJson(Map<String, dynamic> json) => BracketMatchup(
        teamA: json['team_a'] as String,
        teamB: json['team_b'] as String,
        status: json['status'] as String,
        seedA: json['seed_a'] as int?,
        seedB: json['seed_b'] as int?,
        predictedWinner: json['predicted_winner'] as String?,
        winProbability: (json['win_probability'] as num?)?.toDouble(),
        actualWinner: json['actual_winner'] as String?,
        actualHomeScore: json['actual_home_score'] as int?,
        actualAwayScore: json['actual_away_score'] as int?,
      );
}

class BracketRound {
  const BracketRound({required this.round, required this.matchups});

  final String round;
  final List<BracketMatchup> matchups;

  factory BracketRound.fromJson(Map<String, dynamic> json) => BracketRound(
        round: json['round'] as String,
        matchups: (json['matchups'] as List<dynamic>)
            .map((m) => BracketMatchup.fromJson(m as Map<String, dynamic>))
            .toList(),
      );
}

/// A full bracket -- either conference-split (NFL/NBA: two conferences'
/// own round lists plus one cross-conference final matchup) or flat
/// (NCAAFB: one unified 12-team bracket, its own championship already
/// the last round in `rounds`, no separate final-matchup field at all).
/// Exactly one of `conferences`/`rounds` is populated for any given
/// sport -- season_page.dart's own rendering branches on which.
/// A team's display name/abbreviation, as attached by
/// library.serving.common.enrich_bracket_team_names -- the bracket's own
/// equivalent of TeamStanding.abbreviation, just keyed by id in one
/// lookup map instead of carried on every row (a bracket team id appears
/// in several matchups, not just one).
class BracketTeamName {
  const BracketTeamName({this.name, this.abbreviation});

  final String? name;
  final String? abbreviation;

  factory BracketTeamName.fromJson(Map<String, dynamic> json) =>
      BracketTeamName(name: json['name'] as String?, abbreviation: json['abbreviation'] as String?);
}

class BracketProjection {
  const BracketProjection({
    required this.conferences,
    required this.rounds,
    required this.teamNames,
    this.finalMatchup,
    this.champion,
  });

  /// Conference name -> that conference's own round list. Empty for a
  /// flat-bracket sport (NCAAFB).
  final Map<String, List<BracketRound>> conferences;

  /// NCAAFB's own flat round list. Null for a conference-split sport.
  final List<BracketRound>? rounds;

  /// The Super Bowl (NFL) / Finals (NBA) / Cup Championship (NBA Cup) --
  /// null for a flat-bracket sport, where the championship is just the
  /// last entry in `rounds` instead.
  final BracketMatchup? finalMatchup;

  final String? champion;

  /// team_id -> display name/abbreviation, see BracketTeamName's own doc
  /// comment. A team id missing here (shouldn't happen in practice, since
  /// the backend builds this from every id actually appearing in the
  /// bracket) falls back to the raw id, same as teamDisplayFor's own
  /// missing-abbreviation convention.
  final Map<String, BracketTeamName> teamNames;

  factory BracketProjection.fromJson(Map<String, dynamic> json) {
    final conferencesJson = json['conferences'] as Map<String, dynamic>?;
    final roundsJson = json['rounds'] as List<dynamic>?;
    // Different sports name their own cross-conference matchup
    // differently (super_bowl/finals/championship) -- see this class's
    // own doc comment.
    final finalMatchupJson = (json['super_bowl'] ?? json['finals'] ?? json['championship']) as Map<String, dynamic>?;
    final teamNamesJson = json['team_names'] as Map<String, dynamic>?;
    return BracketProjection(
      conferences: (conferencesJson ?? {}).map(
        (conference, rounds) => MapEntry(
          conference,
          (rounds as List<dynamic>).map((r) => BracketRound.fromJson(r as Map<String, dynamic>)).toList(),
        ),
      ),
      rounds: roundsJson?.map((r) => BracketRound.fromJson(r as Map<String, dynamic>)).toList(),
      finalMatchup: finalMatchupJson != null ? BracketMatchup.fromJson(finalMatchupJson) : null,
      champion: json['champion'] as String?,
      teamNames: (teamNamesJson ?? {}).map(
        (teamId, info) => MapEntry(teamId, BracketTeamName.fromJson(info as Map<String, dynamic>)),
      ),
    );
  }
}

class SeasonProjection {
  const SeasonProjection({
    required this.sport,
    required this.season,
    required this.standings,
    required this.leaderboards,
    this.cup,
    this.bracket,
    this.cupBracket,
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

  /// The playoff bracket -- NFL/NCAAFB/NBA only (null for NCAA MBB/PGA/
  /// F1, no elimination-bracket concept exists for those yet/at all).
  /// Best-effort, same "hide, don't error" convention as `cup`/
  /// `leaderboards`.
  final BracketProjection? bracket;

  /// NBA only -- the separate NBA Cup knockout bracket (distinct from
  /// `cup`'s own group-stage standings). Null for every other sport, and
  /// for NBA whenever this season's Cup groups aren't in CUP_GROUPS yet
  /// (same as `cup`) -- see aws-lambdas/nba/predict/season_simulation.py's
  /// own project_cup_knockout_bracket docstring for why this one is
  /// PROJECTED ONLY (no real-vs-actual reconciliation, unlike `bracket`).
  final BracketProjection? cupBracket;

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
        bracket: json['bracket'] != null ? BracketProjection.fromJson(json['bracket'] as Map<String, dynamic>) : null,
        cupBracket: json['cup_bracket'] != null
            ? BracketProjection.fromJson(json['cup_bracket'] as Map<String, dynamic>)
            : null,
      );
}

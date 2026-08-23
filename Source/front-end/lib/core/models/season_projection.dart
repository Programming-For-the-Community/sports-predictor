/// Mirrors GET /{sport}/season's response shape.
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
    this.color,
  });

  final String teamId;
  // Off the team entity; null for an unseeded entity.
  final String? abbreviation;
  final String? color;
  // NCAAFB only -- today's actual (not simulated) National Ranking
  // position, 1-based. Null for NFL and for NCAAFB before the ranking
  // model is promoted or fewer than CFP_FIELD_SIZE teams are tracked.
  final int? currentRank;
  // "AFC East"/"NFC West"/etc for NFL, or the team's conference for every
  // other sport (NCAAFB has no division concept). Used to group standings
  // (see season_page.dart's _groupByDivision).
  final String? division;
  final int wins;
  final int losses;
  final int ties;
  // Monte Carlo season-end projection -- no projectedTies; the simulation
  // never draws a tie.
  final double projectedWins;
  final double projectedLosses;
  final double divisionWinnerProbability;
  final double playoffProbability;
  final double championshipProbability;
  // NBA only -- fraction of simulated paths where this team finishes
  // seeds 7-10 and plays at least one play-in game. Null for every other
  // sport.
  final double? playInProbability;

  // Simulation-derived fields default rather than require -- a standings
  // row is missing them during routine early-season states (simulation
  // skipped until enough teams are tracked or a model is promoted), not a
  // parse failure.
  //
  // division_winner_probability (NFL), conference_champion_probability
  // (NCAAFB), and conference_tournament_champion_probability (NCAA MBB)
  // are the same "won their group" concept at each sport's own
  // granularity, so divisionWinnerProbability reads whichever key its
  // sport's backend sends. Same pattern for playoffProbability
  // (ncaa_tournament_probability -- "made the elimination field") and
  // championshipProbability (national_champion_probability). NCAA MBB's
  // round-by-round probabilities (first_four/round_of_64/sweet_16/
  // elite_eight/final_four/championship_game) aren't surfaced here --
  // the March Madness bracket tab shows that progression directly.
  factory TeamStanding.fromJson(Map<String, dynamic> json) => TeamStanding(
        teamId: json['team_id'] as String,
        division: json['division'] as String? ?? json['conference'] as String?,
        wins: json['wins'] as int,
        losses: json['losses'] as int,
        ties: json['ties'] as int? ?? 0,
        projectedWins: (json['projected_wins'] as num?)?.toDouble() ?? (json['wins'] as int).toDouble(),
        projectedLosses: (json['projected_losses'] as num?)?.toDouble() ?? 0.0,
        divisionWinnerProbability: (json['division_winner_probability'] as num? ??
                    json['conference_champion_probability'] as num? ??
                    json['conference_tournament_champion_probability'] as num?)
                ?.toDouble() ??
            0.0,
        playoffProbability:
            (json['playoff_probability'] as num? ?? json['ncaa_tournament_probability'] as num?)?.toDouble() ?? 0.0,
        championshipProbability:
            (json['championship_probability'] as num? ?? json['national_champion_probability'] as num?)?.toDouble() ??
                0.0,
        playInProbability: (json['play_in_probability'] as num?)?.toDouble(),
        abbreviation: json['abbreviation'] as String?,
        currentRank: json['current_rank'] as int?,
        color: json['color'] as String?,
      );
}

/// One row in a player-prop leaderboard -- `name` falls back to
/// `entityId` the same way PlayerStatLine does.
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

/// One row in an NBA Cup group's standings. `group` itself isn't carried
/// on this row -- it's already the key of the map it lives in (see
/// CupProjection.groups).
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
    this.color,
  });

  final String teamId;
  final String? name;
  final String? abbreviation;
  final String? color;
  // Actual, this-Cup-so-far group-play record (not the team's overall
  // season record).
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
        color: json['color'] as String?,
        groupWins: json['group_wins'] as int? ?? 0,
        groupLosses: json['group_losses'] as int? ?? 0,
        groupWinnerProbability: (json['group_winner_probability'] as num?)?.toDouble() ?? 0.0,
        knockoutProbability: (json['knockout_probability'] as num?)?.toDouble() ?? 0.0,
        cupFinalistProbability: (json['cup_finalist_probability'] as num?)?.toDouble() ?? 0.0,
        championProbability: (json['champion_probability'] as num?)?.toDouble() ?? 0.0,
      );
}

/// NBA Cup (in-season tournament) projection, separate from the
/// end-of-year playoff odds on TeamStanding. NBA only; every other sport's
/// `cup` is null. Also null for NBA whenever the current season's group
/// assignments aren't in CUP_GROUPS yet.
class CupProjection {
  const CupProjection({required this.groups});

  /// "Eastern A"/"Western C"/etc -> that group's teams, sorted
  /// server-side by group_wins descending.
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

/// One resolved bracket slot -- a 3-state design: "projected" (no real
/// game exists yet -- the model's own deterministic pick), "scheduled" (a
/// real game exists, not yet played), or "final" (a real game exists and
/// is completed). seedA/seedB are null on a cross-conference matchup (the
/// Super Bowl/NBA Finals/Cup Championship) since the two sides' seeds
/// aren't on one shared scale.
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
    this.winsA,
    this.winsB,
    this.predictedWinsA,
    this.predictedWinsB,
  });

  final String teamA;
  final String teamB;
  final int? seedA;
  final int? seedB;
  final String status;

  /// Null when a real, scheduled game exists but no prediction has been
  /// computed for it yet.
  final String? predictedWinner;
  final double? winProbability;

  /// Only present when status == "final".
  final String? actualWinner;
  final int? actualHomeScore;
  final int? actualAwayScore;

  /// NBA best-of-7 series record (teamA's/teamB's win count so far this
  /// series). Null for a sport/round with no series concept (NFL/NCAAFB,
  /// and NBA's own Play-In round); present (0/0 at minimum) for every
  /// other NBA playoff round.
  final int? winsA;
  final int? winsB;

  /// The single most likely final record the series ends at, distinct
  /// from winsA/winsB's current/live record. Null once "final" or for a
  /// non-series matchup.
  final int? predictedWinsA;
  final int? predictedWinsB;

  bool get isFinal => status == 'final';

  /// True for a real best-of-7 series slot -- lets the UI show a running
  /// series record instead of a single game's score.
  bool get isSeries => winsA != null && winsB != null;

  factory BracketMatchup.fromJson(Map<String, dynamic> json) => BracketMatchup(
        teamA: json['team_a'] as String,
        teamB: json['team_b'] as String,
        status: json['status'] as String? ?? 'projected',
        seedA: json['seed_a'] as int?,
        seedB: json['seed_b'] as int?,
        predictedWinner: json['predicted_winner'] as String?,
        winProbability: (json['win_probability'] as num?)?.toDouble(),
        actualWinner: json['actual_winner'] as String?,
        actualHomeScore: json['actual_home_score'] as int?,
        actualAwayScore: json['actual_away_score'] as int?,
        winsA: json['wins_a'] as int?,
        winsB: json['wins_b'] as int?,
        predictedWinsA: json['predicted_wins_a'] as int?,
        predictedWinsB: json['predicted_wins_b'] as int?,
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

/// A team's display name/abbreviation for a bracket, keyed by id in one
/// lookup map instead of carried on every row (a bracket team id appears
/// in several matchups).
class BracketTeamName {
  const BracketTeamName({this.name, this.abbreviation, this.color});

  final String? name;
  final String? abbreviation;
  final String? color;

  factory BracketTeamName.fromJson(Map<String, dynamic> json) => BracketTeamName(
        name: json['name'] as String?,
        abbreviation: json['abbreviation'] as String?,
        color: json['color'] as String?,
      );
}

/// A full bracket -- either conference-split (NFL/NBA: two conferences'
/// own round lists plus one cross-conference final matchup) or flat
/// (NCAAFB: one unified bracket, its championship already the last round
/// in `rounds`). Exactly one of `conferences`/`rounds` is populated for
/// any given sport.
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

  /// team_id -> display name/abbreviation. A team id missing here falls
  /// back to the raw id, same as teamDisplayFor's own convention.
  final Map<String, BracketTeamName> teamNames;

  factory BracketProjection.fromJson(Map<String, dynamic> json) {
    final conferencesJson = json['conferences'] as Map<String, dynamic>?;
    final roundsJson = json['rounds'] as List<dynamic>?;
    // Different sports name their own cross-conference matchup
    // differently (super_bowl/finals/championship).
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

/// One conference tournament's own bracket. NCAA MBB only -- one entry per
/// conference with at least 2 tracked members.
class ConferenceBracket {
  const ConferenceBracket({required this.conference, required this.bracket});

  final String conference;
  final BracketProjection bracket;

  factory ConferenceBracket.fromJson(Map<String, dynamic> json) => ConferenceBracket(
        conference: json['conference'] as String,
        bracket: BracketProjection.fromJson(json['bracket'] as Map<String, dynamic>),
      );
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
    this.marchMadnessBracket,
    this.conferenceBrackets,
  });

  final String sport;
  final int? season;

  /// Already sorted by projected_wins descending server-side.
  final List<TeamStanding> standings;

  /// Keyed by TARGET_STAT (e.g. "passing_yards"). Null if the backend
  /// couldn't compute leaderboards.
  final Map<String, List<LeaderboardEntry>>? leaderboards;

  /// NBA only -- see CupProjection's own doc comment for the null cases.
  final CupProjection? cup;

  /// The playoff bracket -- NFL/NCAAFB/NBA only. Null when the sport has
  /// no elimination-bracket concept, or best-effort when unavailable.
  final BracketProjection? bracket;

  /// NBA only -- the separate NBA Cup knockout bracket (distinct from
  /// `cup`'s own group-stage standings). Null for every other sport, and
  /// for NBA before this season's Cup groups are in CUP_GROUPS. Projected
  /// only -- no real-vs-actual reconciliation, unlike `bracket`.
  final BracketProjection? cupBracket;

  /// NCAA MBB only -- the 68-team March Madness bracket (First Four
  /// through Championship, flattened into one `rounds` list, same shape
  /// NCAAFB's `bracket` uses). Null for every other sport, and for NCAA
  /// MBB before conference-tournament champions/at-large seeding can be
  /// resolved.
  final BracketProjection? marchMadnessBracket;

  /// NCAA MBB only -- one bracket per conference tournament. Null for
  /// every other sport.
  final List<ConferenceBracket>? conferenceBrackets;

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
        marchMadnessBracket: json['march_madness_bracket'] != null
            ? BracketProjection.fromJson(json['march_madness_bracket'] as Map<String, dynamic>)
            : null,
        conferenceBrackets: (json['conference_brackets'] as List<dynamic>?)
            ?.map((c) => ConferenceBracket.fromJson(c as Map<String, dynamic>))
            .toList(),
      );
}

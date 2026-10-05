/// Mirrors GET /{sport}/live-scores' response shape (see
/// Source/aws-lambdas/nfl/live-scores/live_scores.py's get_live_scores).
/// One entry per event currently within its poll window (15 minutes
/// before kickoff through completion) -- an event with no entry here just
/// means "not near/at kickoff right now", not an error.
class LiveEventState {
  const LiveEventState({
    required this.live,
    required this.detail,
    required this.homeScore,
    required this.awayScore,
    this.completed = false,
    this.playerStats = const {},
    this.situation,
  });

  // False for an event in its poll window but that hasn't actually
  // kicked off yet (still within the 15-minutes-early lead time) -- see
  // that module's own POLL_START_BEFORE_KICKOFF.
  final bool live;
  // True once ESPN itself reports the game over -- independent of `live`
  // (which flips back to false the moment the game ends) and of this
  // event's own SportEvent.status, which can lag up to 24h behind the
  // real result (the once-daily batch ingest). live_scores.py's own
  // refresh() deliberately keeps serving this event's frozen final
  // state/score/player_stats via this cache for exactly that gap -- a
  // caller that only checks `live` to decide whether to show final
  // stats/a FINAL badge would revert to looking pre-game the instant the
  // game ends. See that module's own refresh() docstring.
  final bool completed;
  // Human-readable game-clock text from ESPN (e.g. "Q3 08:14") -- null
  // before kickoff, when there's nothing meaningful to show yet.
  final String? detail;
  final double? homeScore;
  final double? awayScore;
  // entity_id -> stat_line, from ESPN's own live boxscore -- populated
  // while `live` is true, and (once `completed`) carried forward as the
  // final box score from the last tick it was fetched (see
  // live_scores.py's own refresh()). Absent/empty otherwise.
  final Map<String, Map<String, double>> playerStats;
  // Football only, and only while a play is pending -- null pre-game, at
  // breaks, during reviews, after the game, and for every other sport.
  final FootballSituation? situation;

  factory LiveEventState.fromJson(Map<String, dynamic> json) => LiveEventState(
        live: json['live'] as bool,
        completed: json['completed'] as bool? ?? false,
        detail: json['detail'] as String?,
        homeScore: (json['home_score'] as num?)?.toDouble(),
        awayScore: (json['away_score'] as num?)?.toDouble(),
        playerStats: (json['player_stats'] as Map<String, dynamic>? ?? {}).map(
          (entityId, statLine) => MapEntry(entityId, _numericStats(statLine)),
        ),
        situation: FootballSituation.fromJson(json['situation']),
      );
}

/// Who has the ball and the down, from ESPN's live scoreboard
/// (library/serving/live_scores_common.py's _football_situation).
class FootballSituation {
  const FootballSituation({this.possession, this.downDistance, this.fieldPosition, this.redZone = false});

  /// "home" or "away"; null when ESPN names neither team.
  final String? possession;
  final String? downDistance;
  final String? fieldPosition;
  final bool redZone;

  bool get homeHasBall => possession == 'home';
  bool get awayHasBall => possession == 'away';
  bool get hasDown => downDistance != null || fieldPosition != null;

  /// Null for anything that isn't a usable situation, so one odd value
  /// never fails the whole live-scores response.
  static FootballSituation? fromJson(Object? json) {
    if (json is! Map<String, dynamic>) return null;
    String? text(Object? value) => value is String && value.isNotEmpty ? value : null;
    final possession = json['possession'];
    return FootballSituation(
      possession: possession == 'home' || possession == 'away' ? possession as String : null,
      downDistance: text(json['down_distance']),
      fieldPosition: text(json['field_position']),
      redZone: json['red_zone'] == true,
    );
  }
}

/// A stat line's numeric values only -- ESPN sends placeholders like "--"
/// for some rows, and one of those must not fail the whole live-scores
/// response (it blanked every NCAAFB game's live score, 2026-09-26).
Map<String, double> _numericStats(Object? statLine) {
  if (statLine is! Map<String, dynamic>) return const {};
  return {
    for (final entry in statLine.entries)
      if (entry.value is num) entry.key: (entry.value as num).toDouble(),
  };
}

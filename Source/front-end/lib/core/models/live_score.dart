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

  factory LiveEventState.fromJson(Map<String, dynamic> json) => LiveEventState(
        live: json['live'] as bool,
        completed: json['completed'] as bool? ?? false,
        detail: json['detail'] as String?,
        homeScore: (json['home_score'] as num?)?.toDouble(),
        awayScore: (json['away_score'] as num?)?.toDouble(),
        playerStats: (json['player_stats'] as Map<String, dynamic>? ?? {}).map(
          (entityId, statLine) => MapEntry(
            entityId,
            (statLine as Map<String, dynamic>).map((key, value) => MapEntry(key, (value as num).toDouble())),
          ),
        ),
      );
}

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
    this.playerStats = const {},
  });

  // False for an event in its poll window but that hasn't actually
  // kicked off yet (still within the 15-minutes-early lead time) -- see
  // that module's own POLL_START_BEFORE_KICKOFF.
  final bool live;
  // Human-readable game-clock text from ESPN (e.g. "Q3 08:14") -- null
  // before kickoff, when there's nothing meaningful to show yet.
  final String? detail;
  final double? homeScore;
  final double? awayScore;
  // entity_id -> stat_line, from ESPN's own live boxscore -- only ever
  // populated while `live` is true (see live_scores.py's own refresh()),
  // absent entirely otherwise, same "not fetched this tick" meaning an
  // absent key already carries elsewhere in this model.
  final Map<String, Map<String, double>> playerStats;

  factory LiveEventState.fromJson(Map<String, dynamic> json) => LiveEventState(
        live: json['live'] as bool,
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

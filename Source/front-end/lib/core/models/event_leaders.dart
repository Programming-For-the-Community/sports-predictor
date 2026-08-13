/// Mirrors the `leaders` block on GET /{sport}/predictions/events/{event_id}
/// (see Source/aws-lambdas/nfl/predict/handler.py's _predict_event_leaders)
/// -- optional/nullable throughout since it's a best-effort field that can
/// come back null if the backend couldn't compute it for a given event.
/// `name` is likewise optional -- a raw ESPN id is meaningless in the UI,
/// so this model prefers `name` when the backend includes it and falls
/// back to the id otherwise.
class PlayerStatLine {
  const PlayerStatLine({required this.entityId, required this.name, required this.stats});

  final String entityId;
  final String? name;
  final Map<String, double> stats;

  String get displayName => name ?? entityId;

  factory PlayerStatLine.fromJson(Map<String, dynamic> json) {
    final stats = <String, double>{};
    for (final entry in json.entries) {
      if (entry.key == 'entity_id' || entry.key == 'name') continue;
      if (entry.value is num) stats[entry.key] = (entry.value as num).toDouble();
    }
    return PlayerStatLine(
      entityId: json['entity_id'] as String,
      name: json['name'] as String?,
      stats: stats,
    );
  }
}

class TeamLeaders {
  const TeamLeaders({required this.passing, required this.receiving, required this.rushing, required this.sacks});

  final PlayerStatLine? passing;
  final List<PlayerStatLine> receiving;
  final List<PlayerStatLine> rushing;
  final List<PlayerStatLine> sacks;

  factory TeamLeaders.fromJson(Map<String, dynamic> json) => TeamLeaders(
        passing: json['passing'] != null ? PlayerStatLine.fromJson(json['passing'] as Map<String, dynamic>) : null,
        receiving: _list(json['receiving']),
        rushing: _list(json['rushing']),
        sacks: _list(json['sacks']),
      );

  static List<PlayerStatLine> _list(dynamic value) =>
      (value as List<dynamic>? ?? []).map((e) => PlayerStatLine.fromJson(e as Map<String, dynamic>)).toList();
}

class EventLeaders {
  const EventLeaders({required this.home, required this.away});

  final TeamLeaders home;
  final TeamLeaders away;

  factory EventLeaders.fromJson(Map<String, dynamic> json) => EventLeaders(
        home: TeamLeaders.fromJson(json['home'] as Map<String, dynamic>),
        away: TeamLeaders.fromJson(json['away'] as Map<String, dynamic>),
      );
}

/// Builds an EventLeadersComparison entirely client-side, from the
/// predicted `leaders` block plus GET /{sport}/live-scores' own
/// `player_stats` (LiveEventState.playerStats) -- no separate backend
/// endpoint for this: the predicted side never changes mid-game, and the
/// "actual" side is just whatever the same live-scores poll this page
/// already runs every 30s (event_detail_page.dart) most recently fetched.
/// Reuses PlayerStatLineComparison/TeamLeadersComparison/
/// EventLeadersComparison verbatim -- an in-progress player's live totals
/// are exactly as much an "actual" value as a completed game's final ones,
/// just not yet final.
extension EventLeadersLiveComparison on EventLeaders {
  EventLeadersComparison toLiveComparison(Map<String, Map<String, double>> playerStats) => EventLeadersComparison(
        home: home.toLiveComparison(playerStats),
        away: away.toLiveComparison(playerStats),
      );
}

extension TeamLeadersLiveComparison on TeamLeaders {
  TeamLeadersComparison toLiveComparison(Map<String, Map<String, double>> playerStats) => TeamLeadersComparison(
        passing: passing?.toLiveComparison(playerStats),
        receiving: receiving.map((player) => player.toLiveComparison(playerStats)).toList(),
        rushing: rushing.map((player) => player.toLiveComparison(playerStats)).toList(),
        sacks: sacks.map((player) => player.toLiveComparison(playerStats)).toList(),
      );
}

extension PlayerStatLineLiveComparison on PlayerStatLine {
  PlayerStatLineComparison toLiveComparison(Map<String, Map<String, double>> playerStats) => PlayerStatLineComparison(
        entityId: entityId,
        name: name,
        predicted: stats,
        actual: playerStats[entityId] ?? const {},
      );
}

/// Mirrors the `leaders_comparison` block on a completed event from
/// GET /{sport}/events?status=completed (see
/// Source/library/serving/nfl_reads.py's _leaders_comparison) --
/// predicted-vs-actual player-prop stats for whichever leader candidates
/// had a prediction recorded before the game. Same shape as
/// TeamLeaders/EventLeaders above (passing singular, others lists), just
/// with `predicted`/`actual` sub-maps per player instead of flat stat
/// values -- null throughout under the same "nobody recorded one before
/// the game" condition PredictionComparison already documents at the
/// team level (see event.dart).
class PlayerStatLineComparison {
  const PlayerStatLineComparison({
    required this.entityId, required this.name, required this.predicted, required this.actual,
  });

  final String entityId;
  final String? name;
  final Map<String, double> predicted;
  final Map<String, double> actual;

  String get displayName => name ?? entityId;

  factory PlayerStatLineComparison.fromJson(Map<String, dynamic> json) => PlayerStatLineComparison(
        entityId: json['entity_id'] as String,
        name: json['name'] as String?,
        predicted: _stats(json['predicted']),
        actual: _stats(json['actual']),
      );

  static Map<String, double> _stats(dynamic value) =>
      (value as Map<String, dynamic>? ?? {}).map((key, v) => MapEntry(key, (v as num).toDouble()));
}

class TeamLeadersComparison {
  const TeamLeadersComparison({
    required this.passing, required this.receiving, required this.rushing, required this.sacks,
  });

  final PlayerStatLineComparison? passing;
  final List<PlayerStatLineComparison> receiving;
  final List<PlayerStatLineComparison> rushing;
  final List<PlayerStatLineComparison> sacks;

  factory TeamLeadersComparison.fromJson(Map<String, dynamic> json) => TeamLeadersComparison(
        passing: json['passing'] != null
            ? PlayerStatLineComparison.fromJson(json['passing'] as Map<String, dynamic>)
            : null,
        receiving: _list(json['receiving']),
        rushing: _list(json['rushing']),
        sacks: _list(json['sacks']),
      );

  static List<PlayerStatLineComparison> _list(dynamic value) => (value as List<dynamic>? ?? [])
      .map((e) => PlayerStatLineComparison.fromJson(e as Map<String, dynamic>))
      .toList();
}

class EventLeadersComparison {
  const EventLeadersComparison({required this.home, required this.away});

  final TeamLeadersComparison home;
  final TeamLeadersComparison away;

  factory EventLeadersComparison.fromJson(Map<String, dynamic> json) => EventLeadersComparison(
        home: TeamLeadersComparison.fromJson(json['home'] as Map<String, dynamic>),
        away: TeamLeadersComparison.fromJson(json['away'] as Map<String, dynamic>),
      );
}

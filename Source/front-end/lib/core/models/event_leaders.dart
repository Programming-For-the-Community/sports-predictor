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

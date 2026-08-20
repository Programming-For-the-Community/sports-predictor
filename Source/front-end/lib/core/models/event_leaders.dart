/// Mirrors the `leaders` block on GET /{sport}/predictions/events/{event_id}
/// -- optional/nullable throughout since it's a best-effort field that can
/// come back null. `name` falls back to the raw entity id when the backend
/// omits it.
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

/// Category keys are sport-specific (NFL/NCAAFB: passing/receiving/rushing/
/// sacks; NBA/NCAA MBB: scoring/rebounding/assists -- see
/// team_leaders_panel.dart's own per-sport category config). This model
/// itself stays category-agnostic, just a map of category key ->
/// candidates.
///
/// Every category normalizes to a list here even though NFL's/NCAAFB's own
/// `passing` category is sent as a single object-or-null, not a list -- a
/// team only ever has one starting-QB candidate, so a 0-or-1-element list
/// is lossless and lets every category render through the same
/// list-based widget code.
class TeamLeaders {
  const TeamLeaders(this.categories);

  final Map<String, List<PlayerStatLine>> categories;

  List<PlayerStatLine> operator [](String category) => categories[category] ?? const [];

  factory TeamLeaders.fromJson(Map<String, dynamic> json) =>
      TeamLeaders(json.map((key, value) => MapEntry(key, _normalize(value))));

  static List<PlayerStatLine> _normalize(dynamic value) {
    if (value == null) return const [];
    if (value is List) return value.map((e) => PlayerStatLine.fromJson(e as Map<String, dynamic>)).toList();
    return [PlayerStatLine.fromJson(value as Map<String, dynamic>)];
  }
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
/// `player_stats` (LiveEventState.playerStats). Reuses
/// PlayerStatLineComparison/TeamLeadersComparison/EventLeadersComparison
/// verbatim -- an in-progress player's live totals are exactly as much an
/// "actual" value as a completed game's final ones, just not yet final.
extension EventLeadersLiveComparison on EventLeaders {
  EventLeadersComparison toLiveComparison(Map<String, Map<String, double>> playerStats) => EventLeadersComparison(
        home: home.toLiveComparison(playerStats),
        away: away.toLiveComparison(playerStats),
      );
}

extension TeamLeadersLiveComparison on TeamLeaders {
  TeamLeadersComparison toLiveComparison(Map<String, Map<String, double>> playerStats) => TeamLeadersComparison(
        categories.map((category, players) => MapEntry(
              category,
              players.map((player) => player.toLiveComparison(playerStats)).toList(),
            )),
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
/// GET /{sport}/events?status=completed -- predicted-vs-actual player-prop
/// stats for whichever leader candidates had a prediction recorded before
/// the game. Same shape as TeamLeaders/EventLeaders above (category map,
/// all-list once parsed), just with `predicted`/`actual` sub-maps per
/// player instead of flat stat values.
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
  const TeamLeadersComparison(this.categories);

  final Map<String, List<PlayerStatLineComparison>> categories;

  List<PlayerStatLineComparison> operator [](String category) => categories[category] ?? const [];

  factory TeamLeadersComparison.fromJson(Map<String, dynamic> json) =>
      TeamLeadersComparison(json.map((key, value) => MapEntry(key, _normalize(value))));

  static List<PlayerStatLineComparison> _normalize(dynamic value) {
    if (value == null) return const [];
    if (value is List) {
      return value.map((e) => PlayerStatLineComparison.fromJson(e as Map<String, dynamic>)).toList();
    }
    return [PlayerStatLineComparison.fromJson(value as Map<String, dynamic>)];
  }
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

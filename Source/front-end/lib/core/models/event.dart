import 'event_leaders.dart';

/// Mirrors GET /{sport}/events' response shape (see
/// Source/library/serving/{nfl,ncaafb}_reads.py's own list_events).
class ParticipantResult {
  const ParticipantResult({required this.score, required this.won});

  final double score;
  final bool won;

  factory ParticipantResult.fromJson(Map<String, dynamic> json) => ParticipantResult(
        score: (json['score'] as num).toDouble(),
        won: json['won'] as bool,
      );
}

class Participant {
  const Participant({
    required this.entityId, required this.role, required this.result,
    this.abbreviation, this.conference,
  });

  final String entityId;
  final String role; // 'home' or 'away'
  final ParticipantResult? result; // null until the event is completed
  // Off the participant's own team entity (see enrich_participants) --
  // null on an entity that's never been seeded. NFL prefers its own
  // static/nfl_team_colors.dart table over this (real brand colors,
  // longer-established); every other sport has no such table, so this is
  // its only source. See teamDisplay's own doc comment.
  final String? abbreviation;
  // Also off the team entity -- null for NFL (no conference field on its
  // team entities at all, see espn.py's team_to_entity). Used to group
  // event_list_page.dart's list when present -- see its own grouping doc.
  final String? conference;

  factory Participant.fromJson(Map<String, dynamic> json) => Participant(
        entityId: json['entity_id'] as String,
        role: json['role'] as String,
        result: json['result'] != null ? ParticipantResult.fromJson(json['result'] as Map<String, dynamic>) : null,
        abbreviation: json['abbreviation'] as String?,
        conference: json['conference'] as String?,
      );
}

/// Only present on a completed event -- see handler.py's
/// _prediction_comparison. Null (not just absent) means no prediction was
/// ever logged for this event before it was played, not that the backend
/// failed to compute one.
class PredictionComparison {
  const PredictionComparison({
    required this.predictedHomeWinProbability,
    required this.predictedHomeWon,
    required this.actualHomeWon,
    required this.correct,
    required this.predictedMargin,
    required this.actualMargin,
    required this.predictedHomeScore,
    required this.predictedAwayScore,
    required this.actualHomeScore,
    required this.actualAwayScore,
  });

  final double predictedHomeWinProbability;
  final bool predictedHomeWon;
  final bool actualHomeWon;
  final bool correct;
  final double? predictedMargin;
  final double actualMargin;
  final double? predictedHomeScore;
  final double? predictedAwayScore;
  final double actualHomeScore;
  final double actualAwayScore;

  factory PredictionComparison.fromJson(Map<String, dynamic> json) => PredictionComparison(
        predictedHomeWinProbability: (json['predicted_home_win_probability'] as num).toDouble(),
        predictedHomeWon: json['predicted_home_won'] as bool,
        actualHomeWon: json['actual_home_won'] as bool,
        correct: json['correct'] as bool,
        predictedMargin: (json['predicted_margin'] as num?)?.toDouble(),
        actualMargin: (json['actual_margin'] as num).toDouble(),
        predictedHomeScore: (json['predicted_home_score'] as num?)?.toDouble(),
        predictedAwayScore: (json['predicted_away_score'] as num?)?.toDouble(),
        actualHomeScore: (json['actual_home_score'] as num).toDouble(),
        actualAwayScore: (json['actual_away_score'] as num).toDouble(),
      );
}

class SportEvent {
  const SportEvent({
    required this.eventId,
    required this.eventDate,
    required this.kickoffTime,
    required this.status,
    required this.week,
    required this.round,
    required this.participants,
    required this.predictionComparison,
    required this.leadersComparison,
    this.venueName,
    this.venueCity,
    this.venueState,
  });

  final String eventId;
  final String eventDate;
  // Full ISO 8601 kickoff timestamp -- null for an event ingested before
  // this field existed. Prefer this over eventDate for sorting/display;
  // eventDate is day-only.
  final String? kickoffTime;
  final String status;
  final int? week;
  // Playoff round name (Wild Card/Divisional/Conference Championship/Super
  // Bowl) for a postseason game -- null for regular season, in which case
  // `week` is what the UI should show instead. See handler.py's _round_label.
  final String? round;
  final List<Participant> participants;
  final PredictionComparison? predictionComparison;
  // Stadium name/city/state, straight from ESPN's own venue payload (see
  // normalize/espn.py's scoreboard_event_to_event_item) -- city/state were
  // already ingested as a travel-distance feature input, venueName is new.
  // All three are independently nullable: an indoor neutral-site game can
  // be missing state, and any of them can be absent on an event ingested
  // before this field existed.
  final String? venueName;
  final String? venueCity;
  final String? venueState;
  // Player-prop predicted-vs-actual -- only ever present alongside
  // predictionComparison (both are completed-event-only, see
  // nfl_reads.list_events), same "null means nobody recorded one before
  // the game" condition as that field.
  final EventLeadersComparison? leadersComparison;

  factory SportEvent.fromJson(Map<String, dynamic> json) => SportEvent(
        eventId: json['event_id'] as String,
        eventDate: json['event_date'] as String? ?? '',
        kickoffTime: json['kickoff_time'] as String?,
        status: json['status'] as String? ?? '',
        week: json['week'] as int?,
        round: json['round'] as String?,
        participants: (json['participants'] as List<dynamic>? ?? [])
            .map((p) => Participant.fromJson(p as Map<String, dynamic>))
            .toList(),
        predictionComparison: json['prediction_comparison'] != null
            ? PredictionComparison.fromJson(json['prediction_comparison'] as Map<String, dynamic>)
            : null,
        leadersComparison: json['leaders_comparison'] != null
            ? EventLeadersComparison.fromJson(json['leaders_comparison'] as Map<String, dynamic>)
            : null,
        venueName: json['venue_name'] as String?,
        venueCity: json['venue_city'] as String?,
        venueState: json['venue_state'] as String?,
      );

  Participant get home => participants.firstWhere((p) => p.role == 'home');
  Participant get away => participants.firstWhere((p) => p.role == 'away');

  // "Arrowhead Stadium -- Kansas City, MO", degrading gracefully as
  // pieces go missing (a name with no city, a city with no state, or
  // nothing at all) -- null only when there's truly nothing to show, so
  // callers can skip rendering entirely rather than printing an empty line.
  String? get venueLabel {
    final cityState = [venueCity, venueState].whereType<String>().join(', ');
    final parts = [if (venueName != null) venueName!, if (cityState.isNotEmpty) cityState];
    return parts.isEmpty ? null : parts.join(' -- ');
  }
}

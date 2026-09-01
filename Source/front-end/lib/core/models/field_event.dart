/// Mirrors GET /pga/events' response shape for a "field" (stroke-play)
/// event -- library/serving/pga_reads.py's own _entry. A genuinely
/// different shape from SportEvent/Participant (event.dart): a golfer's
/// own entity IS the participant (no role, no team) -- see
/// design/FRONTEND_STYLE.md's own "field events render a finishing-
/// position distribution, never win/loss shape" guidance.
library;

import 'event_status.dart';
import 'field_prediction.dart' show PgaEventType;

class FieldParticipantResult {
  const FieldParticipantResult({this.finishPosition, this.isTie = false, this.status, this.scoreToPar, this.totalStrokes});

  final int? finishPosition;
  final bool isTie;
  // map_status vocabulary (library/normalize/pga.py): scheduled/finished/
  // cut/made_cut_did_not_finish/withdrawn/... -- null before any result exists.
  final String? status;
  final num? scoreToPar;
  final double? totalStrokes;

  factory FieldParticipantResult.fromJson(Map<String, dynamic> json) => FieldParticipantResult(
        finishPosition: json['finish_position'] as int?,
        isTie: json['is_tie'] as bool? ?? false,
        status: json['status'] as String?,
        scoreToPar: json['score_to_par'] as num?,
        totalStrokes: (json['total_strokes'] as num?)?.toDouble(),
      );
}

class FieldParticipant {
  const FieldParticipant({
    required this.entityId, this.name, this.abbreviation, this.color, this.result,
  });

  final String entityId;
  final String? name;
  // No `role` field read at all -- unlike Participant.fromJson's
  // non-nullable `json['role'] as String` cast, PGA participants carry
  // no role key at all and would crash that parser immediately.
  final String? abbreviation;
  final String? color;
  final FieldParticipantResult? result;

  factory FieldParticipant.fromJson(Map<String, dynamic> json) => FieldParticipant(
        entityId: json['entity_id'] as String,
        name: json['name'] as String?,
        abbreviation: json['abbreviation'] as String?,
        color: json['color'] as String?,
        result: json['result'] != null ? FieldParticipantResult.fromJson(json['result'] as Map<String, dynamic>) : null,
      );
}

class FieldEvent {
  const FieldEvent({
    required this.eventId,
    required this.eventType,
    required this.eventDate,
    required this.status,
    required this.participants,
    this.season,
    this.tournamentName,
    this.endDate,
    this.venueName,
    this.venueCity,
    this.venueState,
  });

  final String eventId;
  // Always 'field' for events this model is used for -- match_play/cup
  // events use TwoSidedPgaPrediction instead (field_prediction.dart), but
  // GET /pga/events itself is homogeneous across all three event_types,
  // so this is kept as a plain string rather than a fixed literal.
  final String eventType;
  final String eventDate;
  final String? endDate;
  final String status;
  final int? season;
  final String? tournamentName;
  final List<FieldParticipant> participants;
  final String? venueName;
  final String? venueCity;
  final String? venueState;

  factory FieldEvent.fromJson(Map<String, dynamic> json) => FieldEvent(
        eventId: json['event_id'] as String,
        eventType: json['event_type'] as String? ?? PgaEventType.field,
        eventDate: json['event_date'] as String? ?? '',
        endDate: json['end_date'] as String?,
        status: json['status'] as String? ?? EventStatus.unknown,
        season: json['season'] as int?,
        tournamentName: json['tournament_name'] as String?,
        participants: (json['participants'] as List<dynamic>? ?? [])
            .map((p) => FieldParticipant.fromJson(p as Map<String, dynamic>))
            .toList(),
        venueName: json['venue_name'] as String?,
        venueCity: json['venue_city'] as String?,
        venueState: json['venue_state'] as String?,
      );

  // "Bellerive Country Club -- St. Louis, MO", degrading gracefully as
  // pieces go missing -- same shape as SportEvent.venueLabel (event.dart).
  String? get venueLabel {
    final cityState = [venueCity, venueState].whereType<String>().join(', ');
    final parts = [if (venueName != null) venueName!, if (cityState.isNotEmpty) cityState];
    return parts.isEmpty ? null : parts.join(' -- ');
  }
}

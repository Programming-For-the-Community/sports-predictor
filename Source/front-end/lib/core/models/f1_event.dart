/// Mirrors GET /f1/events' response shape -- library/serving/f1_reads.py's
/// own _entry. A driver's own entity IS the participant (no role, no
/// team) same as FieldEvent (field_event.dart), but F1 genuinely tracks a
/// constructor per driver AND has two distinct event_types that both
/// return through this one route ("field" for the main race weekend,
/// "sprint" for a Sprint race) -- see f1_event_row.dart for how the list
/// page surfaces that distinction.
library;

import 'event_status.dart';
import 'f1_prediction.dart' show F1EventType;

class F1QualifyingResult {
  const F1QualifyingResult({this.position, this.gapToPoleSeconds});

  final int? position;
  final double? gapToPoleSeconds;

  factory F1QualifyingResult.fromJson(Map<String, dynamic> json) => F1QualifyingResult(
        position: json['position'] as int?,
        gapToPoleSeconds: (json['gap_to_pole_seconds'] as num?)?.toDouble(),
      );
}

class F1ParticipantResult {
  const F1ParticipantResult({
    this.finishPosition, this.gridPosition, this.status, this.points, this.fastestLap, this.lapsCompleted, this.qualifying,
  });

  final int? finishPosition;
  final int? gridPosition;
  // map_status vocabulary (library/normalize/f1.py): finished/classified/
  // dnf/dsq/dns -- null before the race has actually run.
  final String? status;
  final double? points;
  final bool? fastestLap;
  final int? lapsCompleted;
  // Present once qualifying has landed (Saturday), independent of whether
  // the race itself (Sunday) has happened yet.
  final F1QualifyingResult? qualifying;

  factory F1ParticipantResult.fromJson(Map<String, dynamic> json) => F1ParticipantResult(
        finishPosition: json['finish_position'] as int?,
        gridPosition: json['grid_position'] as int?,
        status: json['status'] as String?,
        points: (json['points'] as num?)?.toDouble(),
        fastestLap: json['fastest_lap'] as bool?,
        lapsCompleted: json['laps_completed'] as int?,
        qualifying: json['qualifying'] != null ? F1QualifyingResult.fromJson(json['qualifying'] as Map<String, dynamic>) : null,
      );
}

class F1Participant {
  const F1Participant({required this.entityId, this.name, this.constructorEntityId, this.result});

  final String entityId;
  final String? name;
  final String? constructorEntityId;
  final F1ParticipantResult? result;

  factory F1Participant.fromJson(Map<String, dynamic> json) => F1Participant(
        entityId: json['entity_id'] as String,
        name: json['name'] as String?,
        constructorEntityId: json['constructor_entity_id'] as String?,
        result: json['result'] != null ? F1ParticipantResult.fromJson(json['result'] as Map<String, dynamic>) : null,
      );
}

class F1Event {
  const F1Event({
    required this.eventId,
    required this.eventType,
    required this.eventDate,
    required this.status,
    required this.participants,
    this.season,
    this.week,
    this.raceName,
    this.circuitId,
    this.venueName,
    this.venueCity,
    this.venueState,
  });

  final String eventId;
  // 'field' (main race weekend) or 'sprint' (Sprint race) -- kept as a
  // plain string rather than a fixed literal, same reasoning FieldEvent's
  // own eventType field gives.
  final String eventType;
  final String eventDate;
  final String status;
  final int? season;
  final int? week;
  final String? raceName;
  final String? circuitId;
  final List<F1Participant> participants;
  final String? venueName;
  final String? venueCity;
  final String? venueState;

  bool get isSprint => eventType == F1EventType.sprint;

  factory F1Event.fromJson(Map<String, dynamic> json) => F1Event(
        eventId: json['event_id'] as String,
        eventType: json['event_type'] as String? ?? F1EventType.field,
        eventDate: json['event_date'] as String? ?? '',
        status: json['status'] as String? ?? EventStatus.unknown,
        season: json['season'] as int?,
        week: json['week'] as int?,
        raceName: json['race_name'] as String?,
        circuitId: json['circuit_id'] as String?,
        participants: (json['participants'] as List<dynamic>? ?? [])
            .map((p) => F1Participant.fromJson(p as Map<String, dynamic>))
            .toList(),
        venueName: json['venue_name'] as String?,
        venueCity: json['venue_city'] as String?,
        venueState: json['venue_state'] as String?,
      );

  // "Circuit de Monaco -- Monte Carlo, Monaco", degrading gracefully as
  // pieces go missing -- same shape as FieldEvent.venueLabel.
  String? get venueLabel {
    final cityState = [venueCity, venueState].whereType<String>().join(', ');
    final parts = [if (venueName != null) venueName!, if (cityState.isNotEmpty) cityState];
    return parts.isEmpty ? null : parts.join(' -- ');
  }
}

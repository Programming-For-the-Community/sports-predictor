/// Mirrors GET /{sport}/events' response shape (see
/// Source/aws-lambdas/nfl/predict/handler.py's _list_events) -- deliberately
/// no team display names/colors here, that's static/nfl_team_colors.dart's
/// job, matching how the backend route itself has none either.
class Participant {
  const Participant({required this.entityId, required this.role});

  final String entityId;
  final String role; // 'home' or 'away'

  factory Participant.fromJson(Map<String, dynamic> json) => Participant(
        entityId: json['entity_id'] as String,
        role: json['role'] as String,
      );
}

class SportEvent {
  const SportEvent({
    required this.eventId,
    required this.eventDate,
    required this.status,
    required this.week,
    required this.participants,
  });

  final String eventId;
  final String eventDate;
  final String status;
  final int? week;
  final List<Participant> participants;

  factory SportEvent.fromJson(Map<String, dynamic> json) => SportEvent(
        eventId: json['event_id'] as String,
        eventDate: json['event_date'] as String? ?? '',
        status: json['status'] as String? ?? '',
        week: json['week'] as int?,
        participants: (json['participants'] as List<dynamic>? ?? [])
            .map((p) => Participant.fromJson(p as Map<String, dynamic>))
            .toList(),
      );

  Participant get home => participants.firstWhere((p) => p.role == 'home');
  Participant get away => participants.firstWhere((p) => p.role == 'away');
}

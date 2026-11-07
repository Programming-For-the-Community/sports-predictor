/// The assistant's two modes and the events its runtimes stream back
/// (Source/library/assistant/agent_core.py documents the event shapes).
enum AssistantMode {
  predictions('/assistant/predictions', 'Predictions'),
  infrastructure('/assistant/admin', 'Infrastructure');

  const AssistantMode(this.path, this.label);
  final String path;
  final String label;
}

/// Saved favourite teams, by sport key.
typedef Favorites = Map<String, List<String>>;

Favorites? _favorites(Object? json) {
  if (json is! Map) return null;
  return {for (final entry in json.entries) entry.key as String: [for (final team in entry.value as List) team as String]};
}

sealed class AssistantEvent {
  const AssistantEvent();

  factory AssistantEvent.fromJson(Map<String, dynamic> json) => switch (json['type']) {
        'progress' => AssistantProgress(json['tool'] as String? ?? ''),
        'answer' => AssistantAnswer(
            text: json['text'] as String? ?? '',
            sources: [for (final source in json['sources'] as List? ?? const []) source as String],
            questionsLeft: json['questions_left'] as int?,
            blocked: json['blocked'] as bool? ?? false,
            favorites: _favorites(json['favorites']),
          ),
        'status' => AssistantStatus(
            questionsLeft: json['questions_left'] as int? ?? 0,
            dailyLimit: json['daily_limit'] as int? ?? 0,
            favorites: _favorites(json['favorites']),
          ),
        _ => AssistantFailure(json['code'] as String? ?? 'failed', json['message'] as String? ?? 'Something went wrong. Please try again.'),
      };
}

class AssistantProgress extends AssistantEvent {
  const AssistantProgress(this.tool);
  final String tool;
}

class AssistantAnswer extends AssistantEvent {
  const AssistantAnswer({required this.text, required this.sources, this.questionsLeft, this.blocked = false, this.favorites});
  final String text;
  final List<String> sources;
  final int? questionsLeft;
  final bool blocked;
  final Favorites? favorites;
}

class AssistantStatus extends AssistantEvent {
  const AssistantStatus({required this.questionsLeft, required this.dailyLimit, this.favorites});
  final int questionsLeft;
  final int dailyLimit;
  final Favorites? favorites;
}

class AssistantFailure extends AssistantEvent {
  const AssistantFailure(this.code, this.message);
  final String code;
  final String message;

  bool get isQuota => code == 'quota';
}

class ChatMessage {
  const ChatMessage({required this.fromUser, required this.text, this.sources = const []});
  final bool fromUser;
  final String text;
  final List<String> sources;
}

/// Where the user is in the app, sent as a hint the agent may use.
class AssistantContext {
  const AssistantContext({this.sport, this.eventId, this.eventLabel});
  final String? sport;
  final String? eventId;
  final String? eventLabel;

  Map<String, Object?> toJson() => {if (sport != null) 'sport': sport, if (eventId != null) 'event_id': eventId};
}

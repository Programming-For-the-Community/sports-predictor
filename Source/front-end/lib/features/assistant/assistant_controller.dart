import 'dart:math';

import 'package:flutter_riverpod/legacy.dart';

import 'assistant_client.dart';
import 'assistant_models.dart';

class AssistantState {
  const AssistantState({
    required this.sessionId,
    this.messages = const [],
    this.busy = false,
    this.progressTool,
    this.questionsLeft,
    this.dailyLimit,
    this.favorites = const {},
    this.quotaReached = false,
  });

  final String sessionId;
  final List<ChatMessage> messages;
  final bool busy;
  final String? progressTool;
  final int? questionsLeft;
  final int? dailyLimit;
  final Favorites favorites;
  final bool quotaReached;

  AssistantState copyWith({
    List<ChatMessage>? messages,
    bool? busy,
    String? progressTool,
    bool clearProgress = false,
    int? questionsLeft,
    int? dailyLimit,
    Favorites? favorites,
    bool? quotaReached,
  }) =>
      AssistantState(
        sessionId: sessionId,
        messages: messages ?? this.messages,
        busy: busy ?? this.busy,
        progressTool: clearProgress ? null : progressTool ?? this.progressTool,
        questionsLeft: questionsLeft ?? this.questionsLeft,
        dailyLimit: dailyLimit ?? this.dailyLimit,
        favorites: favorites ?? this.favorites,
        quotaReached: quotaReached ?? this.quotaReached,
      );
}

/// A new conversation id: 64 hex characters, past AgentCore's 33-character minimum.
String newSessionId([Random? random]) {
  final source = random ?? Random.secure();
  return List.generate(32, (_) => source.nextInt(256).toRadixString(16).padLeft(2, '0')).join();
}

/// One conversation per mode. Each mode keeps its own session; "New chat"
/// starts a fresh one.
class AssistantController extends StateNotifier<AssistantState> {
  AssistantController(this._client, this.mode) : super(AssistantState(sessionId: newSessionId()));

  final AssistantClient _client;
  final AssistantMode mode;

  Future<void> loadStatus() => _run({'action': 'status'}, addAnswer: false);

  Future<void> removeFavorite(String sport, String team) =>
      _run({'action': 'remove_favorite', 'sport': sport, 'team': team}, addAnswer: false);

  Future<void> ask(String question, {AssistantContext context = const AssistantContext()}) async {
    final text = question.trim();
    if (text.isEmpty || state.busy || state.quotaReached) return;
    state = state.copyWith(messages: [...state.messages, ChatMessage(fromUser: true, text: text)]);
    await _run({'message': text, ...context.toJson()}, addAnswer: true);
  }

  void newChat() {
    state = AssistantState(
      sessionId: newSessionId(),
      questionsLeft: state.questionsLeft,
      dailyLimit: state.dailyLimit,
      favorites: state.favorites,
      quotaReached: state.quotaReached,
    );
  }

  Future<void> _run(Map<String, Object?> body, {required bool addAnswer}) async {
    state = state.copyWith(busy: true, clearProgress: true);
    try {
      await for (final event in _client.send(mode, state.sessionId, body)) {
        _apply(event, addAnswer: addAnswer);
      }
    } catch (_) {
      _apply(const AssistantFailure('failed', 'The assistant couldn\'t be reached. Please try again.'), addAnswer: addAnswer);
    } finally {
      state = state.copyWith(busy: false, clearProgress: true);
    }
  }

  void _apply(AssistantEvent event, {required bool addAnswer}) {
    switch (event) {
      case AssistantProgress(:final tool):
        state = state.copyWith(progressTool: tool);
      case AssistantAnswer():
        state = state.copyWith(
          messages: [...state.messages, ChatMessage(fromUser: false, text: event.text, sources: event.sources)],
          questionsLeft: event.questionsLeft,
          favorites: event.favorites,
        );
      case AssistantStatus():
        state = state.copyWith(questionsLeft: event.questionsLeft, dailyLimit: event.dailyLimit, favorites: event.favorites);
      case AssistantFailure():
        state = state.copyWith(
          messages: addAnswer ? [...state.messages, ChatMessage(fromUser: false, text: event.message)] : null,
          quotaReached: event.isQuota ? true : null,
          questionsLeft: event.isQuota ? 0 : null,
        );
    }
  }
}

final assistantControllerProvider = StateNotifierProvider.family<AssistantController, AssistantState, AssistantMode>(
  (ref, mode) => AssistantController(ref.watch(assistantClientProvider), mode),
);

/// Whether the web layout's docked assistant panel is open.
final assistantDockOpenProvider = StateProvider<bool>((ref) => false);

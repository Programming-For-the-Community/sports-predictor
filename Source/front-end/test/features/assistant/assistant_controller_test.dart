import 'dart:math';

import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/features/assistant/assistant_client.dart';
import 'package:front_end/features/assistant/assistant_controller.dart';
import 'package:front_end/features/assistant/assistant_models.dart';

class _FakeClient implements AssistantClient {
  _FakeClient(this.responses);
  final List<List<AssistantEvent>> responses;
  final List<Map<String, Object?>> bodies = [];
  final List<String> sessions = [];

  @override
  Stream<AssistantEvent> send(AssistantMode mode, String sessionId, Map<String, Object?> body) {
    bodies.add(body);
    sessions.add(sessionId);
    return Stream.fromIterable(responses.removeAt(0));
  }

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

void main() {
  test('a question adds both turns and the counts the agent sends back', () async {
    final client = _FakeClient([
      [const AssistantProgress('list_events'), const AssistantAnswer(text: 'DET -6.5', sources: ['list_events'], questionsLeft: 49, favorites: {'nfl': ['DET']})],
    ]);
    final controller = AssistantController(client, AssistantMode.predictions);

    await controller.ask('Why DET?', context: const AssistantContext(sport: 'nfl', eventId: '401'));

    expect(client.bodies.single, {'message': 'Why DET?', 'sport': 'nfl', 'event_id': '401'});
    expect([for (final m in controller.state.messages) m.text], ['Why DET?', 'DET -6.5']);
    expect(controller.state.messages.last.sources, ['list_events']);
    expect(controller.state.questionsLeft, 49);
    expect(controller.state.favorites, {'nfl': ['DET']});
    expect(controller.state.busy, isFalse);
  });

  test('the daily limit locks the input and blank questions are ignored', () async {
    final client = _FakeClient([[const AssistantFailure('quota', 'Used up')]]);
    final controller = AssistantController(client, AssistantMode.predictions);

    await controller.ask('One more?');
    await controller.ask('And another?');
    await controller.ask('   ');

    expect(controller.state.quotaReached, isTrue);
    expect(controller.state.questionsLeft, 0);
    expect(client.bodies, hasLength(1));
  });

  test('status and favourite removal add no messages; a new chat keeps the counts but not the session', () async {
    final client = _FakeClient([
      [const AssistantStatus(questionsLeft: 42, dailyLimit: 50, favorites: {'nfl': ['DET', 'GB']})],
      [const AssistantStatus(questionsLeft: 42, dailyLimit: 50, favorites: {'nfl': ['DET']})],
    ]);
    final controller = AssistantController(client, AssistantMode.predictions);

    await controller.loadStatus();
    await controller.removeFavorite('nfl', 'GB');
    final firstSession = controller.state.sessionId;
    controller.newChat();

    expect(client.bodies, [{'action': 'status'}, {'action': 'remove_favorite', 'sport': 'nfl', 'team': 'GB'}]);
    expect(controller.state.messages, isEmpty);
    expect(controller.state.favorites, {'nfl': ['DET']});
    expect(controller.state.questionsLeft, 42);
    expect(controller.state.sessionId, isNot(firstSession));
  });

  test('session ids are long enough for AgentCore', () {
    expect(newSessionId(Random(1)), hasLength(64));
  });
}

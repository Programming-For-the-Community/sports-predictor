import 'dart:convert';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:front_end/core/auth/auth_repository.dart';
import 'package:front_end/core/auth/cognito_auth_client.dart';
import 'package:front_end/features/assistant/assistant_client.dart';
import 'package:front_end/features/assistant/assistant_models.dart';

class _FakeAuthRepository extends AuthRepository {
  _FakeAuthRepository() : super(authClient: CognitoAuthClient(httpClient: MockClient((r) async => http.Response('{}', 200))));

  final List<bool> forceRefreshCalls = [];

  @override
  Future<String> getValidIdToken({bool forceRefresh = false}) async {
    forceRefreshCalls.add(forceRefresh);
    return forceRefresh ? 'fresh-token' : 'token';
  }
}

AssistantClient _client(Future<http.StreamedResponse> Function(http.BaseRequest, http.ByteStream) handler, _FakeAuthRepository auth) {
  final container = ProviderContainer(overrides: [authRepositoryProvider.overrideWith((ref) => auth)]);
  addTearDown(container.dispose);
  final ref = container.read(Provider<Ref>((ref) => ref));
  return AssistantClient(ref, httpClient: MockClient.streaming(handler));
}

http.StreamedResponse _sse(List<Map<String, Object?>> events, {int status = 200}) {
  final body = events.map((e) => 'data: ${jsonEncode(e)}\n\n').join();
  return http.StreamedResponse(Stream.value(utf8.encode(body)), status);
}

void main() {
  setUp(() => SharedPreferences.setMockInitialValues({}));

  test('streams progress then the answer, with the bearer token and session header', () async {
    late http.BaseRequest seen;
    final client = _client((request, body) async {
      seen = request;
      return _sse([
        {'type': 'progress', 'tool': 'list_events'},
        {'type': 'answer', 'text': 'DET -6.5', 'sources': ['list_events'], 'questions_left': 49, 'favorites': {'nfl': ['DET']}},
      ]);
    }, _FakeAuthRepository());

    final events = await client.send(AssistantMode.predictions, 's' * 64, {'message': 'Why DET?'}).toList();

    expect(seen.method, 'POST');
    expect(seen.url.path, '/assistant/predictions');
    expect(seen.headers['Authorization'], 'Bearer token');
    expect(seen.headers[AssistantClient.sessionHeader], 's' * 64);
    expect(events[0], isA<AssistantProgress>().having((e) => e.tool, 'tool', 'list_events'));
    final answer = events[1] as AssistantAnswer;
    expect(answer.text, 'DET -6.5');
    expect(answer.questionsLeft, 49);
    expect(answer.favorites, {'nfl': ['DET']});
  });

  test('a 401 is retried once with a refreshed token', () async {
    final auth = _FakeAuthRepository();
    var calls = 0;
    final client = _client((request, body) async {
      calls++;
      return calls == 1 ? _sse([], status: 401) : _sse([{'type': 'status', 'questions_left': 50, 'daily_limit': 50}]);
    }, auth);

    final events = await client.send(AssistantMode.infrastructure, 'x' * 64, {'action': 'status'}).toList();

    expect(auth.forceRefreshCalls, [false, true]);
    expect(events.single, isA<AssistantStatus>());
  });

  test('a refused or throttled request becomes a failure event', () async {
    final forbidden = _client((request, body) async => _sse([], status: 403), _FakeAuthRepository());
    final throttled = _client((request, body) async => _sse([], status: 429), _FakeAuthRepository());

    expect((await forbidden.send(AssistantMode.infrastructure, 'x' * 64, {}).single as AssistantFailure).code, 'forbidden');
    expect((await throttled.send(AssistantMode.predictions, 'x' * 64, {}).single as AssistantFailure).code, 'rate_limited');
  });

  test('an error event from the agent keeps its code', () {
    final event = AssistantEvent.fromJson({'type': 'error', 'code': 'quota', 'message': 'Used up'});
    expect(event, isA<AssistantFailure>().having((e) => e.isQuota, 'isQuota', isTrue));
  });
}

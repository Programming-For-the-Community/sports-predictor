import 'dart:convert';
import 'dart:io';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:front_end/core/api/api_client.dart';
import 'package:front_end/core/api/api_exception.dart';
import 'package:front_end/core/auth/auth_repository.dart';
import 'package:front_end/core/auth/cognito_auth_client.dart';

class _FakeAuthRepository extends AuthRepository {
  _FakeAuthRepository(this._tokens)
      : super(authClient: CognitoAuthClient(httpClient: MockClient((r) async => http.Response('{}', 200))));

  final List<Object> _tokens; // String tokens, or an Exception to throw, consumed in order
  final List<bool> forceRefreshCalls = [];

  @override
  Future<String> getValidIdToken({bool forceRefresh = false}) async {
    forceRefreshCalls.add(forceRefresh);
    final next = _tokens.removeAt(0);
    if (next is Exception) throw next;
    return next as String;
  }
}

({ApiClient client, _FakeAuthRepository authRepo}) _buildClient(
  List<Object> tokens,
  Future<http.Response> Function(http.Request) handler,
) {
  final authRepo = _FakeAuthRepository(tokens);
  final container = ProviderContainer(overrides: [
    authRepositoryProvider.overrideWith((ref) => authRepo),
  ]);
  final refProvider = Provider<Ref>((ref) => ref);
  final ref = container.read(refProvider);
  final client = ApiClient(ref, httpClient: MockClient(handler));
  return (client: client, authRepo: authRepo);
}

void main() {
  setUp(() {
    SharedPreferences.setMockInitialValues({});
  });

  test('get() decodes a successful JSON response', () async {
    final built = _buildClient(['token-1'], (request) async {
      expect(request.headers['Authorization'], 'token-1');
      return http.Response(jsonEncode({'ok': true}), 200);
    });

    final result = await built.client.get('/nfl/events');

    expect(result, {'ok': true});
  });

  test('get() passes query parameters through to the request URI', () async {
    Uri? capturedUri;
    final built = _buildClient(['token-1'], (request) async {
      capturedUri = request.url;
      return http.Response(jsonEncode({}), 200);
    });

    await built.client.get('/nfl/events', queryParameters: {'status': 'scheduled'});

    expect(capturedUri?.queryParameters['status'], 'scheduled');
  });

  test('get() returns null for an empty response body', () async {
    final built = _buildClient(['token-1'], (request) async => http.Response('', 200));

    final result = await built.client.get('/nfl/events');

    expect(result, isNull);
  });

  test('get() throws ApiException with the server-provided error message on a non-2xx response', () async {
    final built = _buildClient(['token-1'], (request) async {
      return http.Response(jsonEncode({'error': 'event not found'}), 404);
    });

    await expectLater(
      built.client.get('/nfl/events/bad-id'),
      throwsA(isA<ApiException>()
          .having((e) => e.statusCode, 'statusCode', 404)
          .having((e) => e.message, 'message', 'event not found')),
    );
  });

  test('get() falls back to the raw body when a non-2xx response is not JSON', () async {
    final built = _buildClient(['token-1'], (request) async => http.Response('Internal Server Error', 500));

    await expectLater(
      built.client.get('/nfl/events'),
      throwsA(isA<ApiException>()
          .having((e) => e.statusCode, 'statusCode', 500)
          .having((e) => e.message, 'message', 'Internal Server Error')),
    );
  });

  test('get() retries once with a forced token refresh on a 401', () async {
    var callCount = 0;
    final built = _buildClient(['stale-token', 'fresh-token'], (request) async {
      callCount++;
      if (request.headers['Authorization'] == 'stale-token') {
        return http.Response('Unauthorized', 401);
      }
      return http.Response(jsonEncode({'ok': true}), 200);
    });

    final result = await built.client.get('/nfl/events');

    expect(result, {'ok': true});
    expect(callCount, 2);
    expect(built.authRepo.forceRefreshCalls, [false, true]);
  });

  test('get() does not retry a second time if the forced-refresh attempt is also a 401', () async {
    var callCount = 0;
    final built = _buildClient(['token-1', 'token-2'], (request) async {
      callCount++;
      return http.Response('Unauthorized', 401);
    });

    await expectLater(built.client.get('/nfl/events'), throwsA(isA<ApiException>().having((e) => e.statusCode, 'statusCode', 401)));
    expect(callCount, 2);
  });

  test('get() retries a 429 and returns the eventual success', () async {
    var callCount = 0;
    final built = _buildClient(['token-1', 'token-1'], (request) async {
      callCount++;
      if (callCount == 1) return http.Response('Too Many Requests', 429);
      return http.Response(jsonEncode({'ok': true}), 200);
    });

    final result = await built.client.get('/nfl/events');

    expect(result, {'ok': true});
    expect(callCount, 2);
  });

  test('get() gives up after exhausting its 429 retry budget', () async {
    var callCount = 0;
    final built = _buildClient(
      ['token-1', 'token-1', 'token-1', 'token-1', 'token-1'],
      (request) async {
        callCount++;
        return http.Response('Too Many Requests', 429);
      },
    );

    await expectLater(built.client.get('/nfl/events'), throwsA(isA<ApiException>().having((e) => e.statusCode, 'statusCode', 429)));
    // 1 initial attempt + 4 retries.
    expect(callCount, 5);
  });

  test('get() surfaces getValidIdToken failures instead of swallowing them', () async {
    final built = _buildClient([Exception('refresh token expired')], (request) async {
      fail('should never reach the network without a token');
    });

    await expectLater(built.client.get('/nfl/events'), throwsA(isA<Exception>()));
  });

  test('get() rethrows when the underlying http call itself throws', () async {
    final built = _buildClient(['token-1'], (request) async {
      throw const SocketException('Connection refused');
    });

    await expectLater(built.client.get('/nfl/events'), throwsA(isA<SocketException>()));
  });

  test('apiClientProvider builds an ApiClient bound to the container', () {
    SharedPreferences.setMockInitialValues({});
    final container = ProviderContainer();
    addTearDown(container.dispose);

    expect(container.read(apiClientProvider), isA<ApiClient>());
  });
}

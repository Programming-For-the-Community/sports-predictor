import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:front_end/core/auth/auth_repository.dart';
import 'package:front_end/core/auth/cognito_auth_client.dart';

http.Response _tokenResponse({String access = 'access', String id = 'id', String? refresh = 'refresh', int expiresIn = 3600}) {
  final result = <String, dynamic>{'AccessToken': access, 'IdToken': id, 'ExpiresIn': expiresIn};
  if (refresh != null) result['RefreshToken'] = refresh;
  return http.Response(jsonEncode({'AuthenticationResult': result}), 200);
}

Future<AuthState> _firstRealState(AuthRepository repo) => repo.stream.firstWhere((s) => s is! AuthInitial);

void main() {
  setUp(() {
    SharedPreferences.setMockInitialValues({});
  });

  test('starts unauthenticated when nothing is persisted', () async {
    final repo = AuthRepository(authClient: CognitoAuthClient(httpClient: MockClient((r) async => _tokenResponse())));

    final state = await _firstRealState(repo);

    expect(state, isA<AuthUnauthenticated>());
  });

  test('login success moves state to AuthAuthenticated', () async {
    final repo = AuthRepository(
      authClient: CognitoAuthClient(httpClient: MockClient((r) async => _tokenResponse(access: 'a1'))),
    );
    await _firstRealState(repo);

    await repo.login(username: 'chamar', password: 'hunter2');

    expect(repo.state, isA<AuthAuthenticated>());
    expect((repo.state as AuthAuthenticated).tokens.accessToken, 'a1');
  });

  test('NEW_PASSWORD_REQUIRED challenge then respondToNewPassword completes login', () async {
    var challengeIssued = false;
    final repo = AuthRepository(
      authClient: CognitoAuthClient(
        httpClient: MockClient((request) async {
          if (!challengeIssued) {
            challengeIssued = true;
            return http.Response(
              jsonEncode({'ChallengeName': 'NEW_PASSWORD_REQUIRED', 'Session': 'sess-1'}),
              200,
            );
          }
          return _tokenResponse(access: 'a2');
        }),
      ),
    );
    await _firstRealState(repo);

    await repo.login(username: 'chamar', password: 'temp-pass');
    expect(repo.state, isA<AuthNeedsNewPassword>());

    await repo.respondToNewPassword('NewPass123');
    expect(repo.state, isA<AuthAuthenticated>());
    expect((repo.state as AuthAuthenticated).tokens.accessToken, 'a2');
  });

  test('respondToNewPassword outside the challenge state throws', () async {
    final repo = AuthRepository(authClient: CognitoAuthClient(httpClient: MockClient((r) async => _tokenResponse())));
    await _firstRealState(repo);

    expect(() => repo.respondToNewPassword('whatever'), throwsStateError);
  });

  test('getValidAccessToken refreshes proactively when near expiry', () async {
    var refreshCalls = 0;
    final repo = AuthRepository(
      authClient: CognitoAuthClient(
        httpClient: MockClient((request) async {
          final body = jsonDecode(request.body) as Map<String, dynamic>;
          if (body['AuthFlow'] == 'REFRESH_TOKEN_AUTH') {
            refreshCalls++;
            return _tokenResponse(access: 'refreshed', refresh: null);
          }
          // Login response: expires in 30s -- already within the 60s window.
          return _tokenResponse(access: 'initial', expiresIn: 30);
        }),
      ),
    );
    await _firstRealState(repo);
    await repo.login(username: 'chamar', password: 'hunter2');

    final token = await repo.getValidAccessToken();

    expect(token, 'refreshed');
    expect(refreshCalls, 1);
  });

  test('getValidAccessToken transitions to AuthUnauthenticated when the refresh token itself is rejected', () async {
    final repo = AuthRepository(
      authClient: CognitoAuthClient(
        httpClient: MockClient((request) async {
          final body = jsonDecode(request.body) as Map<String, dynamic>;
          if (body['AuthFlow'] == 'REFRESH_TOKEN_AUTH') {
            return http.Response(
              jsonEncode({'__type': 'NotAuthorizedException', 'message': 'Refresh Token has expired'}),
              400,
            );
          }
          // Login response: expires in 30s -- already within the 60s window.
          return _tokenResponse(access: 'initial', expiresIn: 30);
        }),
      ),
    );
    await _firstRealState(repo);
    await repo.login(username: 'chamar', password: 'hunter2');

    await expectLater(repo.getValidAccessToken(), throwsA(isA<CognitoException>()));

    expect(repo.state, isA<AuthUnauthenticated>());
    final prefs = await SharedPreferences.getInstance();
    expect(prefs.getString('cognito_tokens'), isNull);
  });

  test('logout clears state and persisted storage', () async {
    final repo = AuthRepository(authClient: CognitoAuthClient(httpClient: MockClient((r) async => _tokenResponse())));
    await _firstRealState(repo);
    await repo.login(username: 'chamar', password: 'hunter2');
    expect(repo.state, isA<AuthAuthenticated>());

    await repo.logout();

    expect(repo.state, isA<AuthUnauthenticated>());
    final prefs = await SharedPreferences.getInstance();
    expect(prefs.getString('cognito_tokens'), isNull);
  });
}

import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:front_end/core/auth/auth_repository.dart';
import 'package:front_end/core/auth/cognito_auth_client.dart';
import 'package:front_end/core/auth/token_store.dart';

import '../../support/cognito_srp_test_support.dart';

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
    final repo = AuthRepository(authClient: CognitoAuthClient(srpFactory: FakeCognitoSrp.new, httpClient: srpAwareMockClient((r) async => _tokenResponse())));

    final state = await _firstRealState(repo);

    expect(state, isA<AuthUnauthenticated>());
  });

  test('login success moves state to AuthAuthenticated', () async {
    final repo = AuthRepository(
      authClient: CognitoAuthClient(srpFactory: FakeCognitoSrp.new, httpClient: srpAwareMockClient((r) async => _tokenResponse(access: 'a1'))),
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
        srpFactory: FakeCognitoSrp.new,
        httpClient: srpAwareMockClient((request) async {
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
    final repo = AuthRepository(authClient: CognitoAuthClient(srpFactory: FakeCognitoSrp.new, httpClient: srpAwareMockClient((r) async => _tokenResponse())));
    await _firstRealState(repo);

    expect(() => repo.respondToNewPassword('whatever'), throwsStateError);
  });

  test('getValidIdToken refreshes proactively when near expiry', () async {
    var refreshCalls = 0;
    final repo = AuthRepository(
      authClient: CognitoAuthClient(
        srpFactory: FakeCognitoSrp.new,
        httpClient: srpAwareMockClient((request) async {
          final body = jsonDecode(request.body) as Map<String, dynamic>;
          if (body['AuthFlow'] == 'REFRESH_TOKEN_AUTH') {
            refreshCalls++;
            return _tokenResponse(id: 'refreshed', refresh: null);
          }
          // Login response: expires in 30s -- already within the 60s window.
          return _tokenResponse(id: 'initial', expiresIn: 30);
        }),
      ),
    );
    await _firstRealState(repo);
    await repo.login(username: 'chamar', password: 'hunter2');

    final token = await repo.getValidIdToken();

    expect(token, 'refreshed');
    expect(refreshCalls, 1);
  });

  test('getValidIdToken(forceRefresh: true) refreshes even when the token looks fresh locally', () async {
    var refreshCalls = 0;
    final repo = AuthRepository(
      authClient: CognitoAuthClient(
        srpFactory: FakeCognitoSrp.new,
        httpClient: srpAwareMockClient((request) async {
          final body = jsonDecode(request.body) as Map<String, dynamic>;
          if (body['AuthFlow'] == 'REFRESH_TOKEN_AUTH') {
            refreshCalls++;
            return _tokenResponse(id: 'refreshed', refresh: null);
          }
          // Login response: expires in 3600s -- nowhere near the 60s window,
          // so a plain getValidIdToken() call would NOT refresh this.
          return _tokenResponse(id: 'initial', expiresIn: 3600);
        }),
      ),
    );
    await _firstRealState(repo);
    await repo.login(username: 'chamar', password: 'hunter2');

    final token = await repo.getValidIdToken(forceRefresh: true);

    expect(token, 'refreshed');
    expect(refreshCalls, 1);
  });

  test('getValidIdToken transitions to AuthUnauthenticated when the refresh token itself is rejected', () async {
    final repo = AuthRepository(
      authClient: CognitoAuthClient(
        srpFactory: FakeCognitoSrp.new,
        httpClient: srpAwareMockClient((request) async {
          final body = jsonDecode(request.body) as Map<String, dynamic>;
          if (body['AuthFlow'] == 'REFRESH_TOKEN_AUTH') {
            return http.Response(
              jsonEncode({'__type': 'NotAuthorizedException', 'message': 'Refresh Token has expired'}),
              400,
            );
          }
          // Login response: expires in 30s -- already within the 60s window.
          return _tokenResponse(id: 'initial', expiresIn: 30);
        }),
      ),
    );
    await _firstRealState(repo);
    await repo.login(username: 'chamar', password: 'hunter2');

    await expectLater(repo.getValidIdToken(), throwsA(isA<CognitoException>()));

    expect(repo.state, isA<AuthUnauthenticated>());
    final prefs = await SharedPreferences.getInstance();
    expect(prefs.getString('cognito_tokens'), isNull);
  });

  test('a session persisted past inactivityTtl restores as unauthenticated', () async {
    final staleActivity = DateTime.now().subtract(inactivityTtl + const Duration(minutes: 1));
    SharedPreferences.setMockInitialValues({
      'cognito_tokens': jsonEncode(CognitoTokens(
        accessToken: 'a', idToken: 'i', refreshToken: 'r',
        expiresAt: DateTime.now().add(const Duration(hours: 1)), // token itself still valid
      ).toJson()),
      'last_activity_at': staleActivity.toIso8601String(),
    });
    final repo = AuthRepository(authClient: CognitoAuthClient(srpFactory: FakeCognitoSrp.new, httpClient: srpAwareMockClient((r) async => _tokenResponse())));

    final state = await _firstRealState(repo);

    expect(state, isA<AuthUnauthenticated>());
    final prefs = await SharedPreferences.getInstance();
    expect(prefs.getString('cognito_tokens'), isNull);
  });

  test('getValidIdToken forces re-login once inactivityTtl has passed, even with a still-valid token', () async {
    final repo = AuthRepository(
      authClient: CognitoAuthClient(srpFactory: FakeCognitoSrp.new, httpClient: srpAwareMockClient((r) async => _tokenResponse(expiresIn: 3600))),
    );
    await _firstRealState(repo);
    await repo.login(username: 'chamar', password: 'hunter2');

    // Simulate time passing without any further activity being recorded --
    // the token itself (1hr expiry) is nowhere near needing a refresh.
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(
      'last_activity_at',
      DateTime.now().subtract(inactivityTtl + const Duration(minutes: 1)).toIso8601String(),
    );

    await expectLater(repo.getValidIdToken(), throwsStateError);

    expect(repo.state, isA<AuthUnauthenticated>());
    expect(prefs.getString('cognito_tokens'), isNull);
  });

  test('getValidIdToken called before restore finishes waits for it instead of throwing', () async {
    SharedPreferences.setMockInitialValues({
      'cognito_tokens': jsonEncode(CognitoTokens(
        accessToken: 'a', idToken: 'persisted-id', refreshToken: 'r',
        expiresAt: DateTime.now().add(const Duration(hours: 1)),
      ).toJson()),
      'last_activity_at': DateTime.now().toIso8601String(),
    });
    final repo = AuthRepository(authClient: CognitoAuthClient(srpFactory: FakeCognitoSrp.new, httpClient: srpAwareMockClient((r) async => _tokenResponse())));

    // No await on _firstRealState here -- mirrors a page that builds and
    // fetches immediately on a browser refresh, before _restoreSession's
    // SharedPreferences read has had a chance to run.
    final token = await repo.getValidIdToken();

    expect(token, 'persisted-id');
    expect(repo.state, isA<AuthAuthenticated>());
  });

  test('a persisted session near expiry refreshes on restore and persists the new tokens', () async {
    SharedPreferences.setMockInitialValues({
      'cognito_tokens': jsonEncode(CognitoTokens(
        accessToken: 'a', idToken: 'old-id', refreshToken: 'r',
        expiresAt: DateTime.now().add(const Duration(seconds: 5)),
      ).toJson()),
      'last_activity_at': DateTime.now().toIso8601String(),
    });
    final repo = AuthRepository(
      authClient: CognitoAuthClient(srpFactory: FakeCognitoSrp.new, httpClient: srpAwareMockClient((r) async => _tokenResponse(id: 'refreshed', refresh: null))),
    );

    final state = await _firstRealState(repo);

    expect(state, isA<AuthAuthenticated>());
    final prefs = await SharedPreferences.getInstance();
    expect(prefs.getString('cognito_tokens'), contains('refreshed'));
  });

  test('logout clears state and persisted storage', () async {
    final repo = AuthRepository(authClient: CognitoAuthClient(srpFactory: FakeCognitoSrp.new, httpClient: srpAwareMockClient((r) async => _tokenResponse())));
    await _firstRealState(repo);
    await repo.login(username: 'chamar', password: 'hunter2');
    expect(repo.state, isA<AuthAuthenticated>());

    await repo.logout();

    expect(repo.state, isA<AuthUnauthenticated>());
    final prefs = await SharedPreferences.getInstance();
    expect(prefs.getString('cognito_tokens'), isNull);
  });

  test('recordActivity resets a session that would otherwise have gone inactive', () async {
    final repo = AuthRepository(authClient: CognitoAuthClient(srpFactory: FakeCognitoSrp.new, httpClient: srpAwareMockClient((r) async => _tokenResponse())));
    await _firstRealState(repo);
    await repo.login(username: 'chamar', password: 'hunter2');

    // Same "time passed with nothing touching activity" setup as the
    // inactivityTtl test above -- the difference here is a pointer
    // interaction (recordActivity) happens before the next request.
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(
      'last_activity_at',
      DateTime.now().subtract(inactivityTtl + const Duration(minutes: 1)).toIso8601String(),
    );

    await repo.recordActivity();

    expect(await repo.getValidIdToken(), isNotEmpty);
    expect(repo.state, isA<AuthAuthenticated>());
  });

  test('recordActivity throttles repeated calls instead of writing every time', () async {
    final repo = AuthRepository(authClient: CognitoAuthClient(srpFactory: FakeCognitoSrp.new, httpClient: srpAwareMockClient((r) async => _tokenResponse())));
    await _firstRealState(repo);
    await repo.login(username: 'chamar', password: 'hunter2');

    final prefs = await SharedPreferences.getInstance();
    final afterLogin = prefs.getString('last_activity_at');

    // Immediately-repeated calls (well within the throttle window) must
    // not overwrite the timestamp the login itself just wrote.
    await repo.recordActivity();
    await repo.recordActivity();

    expect(prefs.getString('last_activity_at'), afterLogin);
  });

  test('recordActivity does nothing while unauthenticated', () async {
    final repo = AuthRepository(authClient: CognitoAuthClient(srpFactory: FakeCognitoSrp.new, httpClient: srpAwareMockClient((r) async => _tokenResponse())));
    await _firstRealState(repo);
    expect(repo.state, isA<AuthUnauthenticated>());

    await repo.recordActivity();

    final prefs = await SharedPreferences.getInstance();
    expect(prefs.getString('last_activity_at'), isNull);
  });

  group('rolling session (Android app)', () {
    String persisted({String id = 'old-id', String refresh = 'r1', Duration expiresIn = const Duration(hours: 1)}) => jsonEncode(
          CognitoTokens(accessToken: 'a', idToken: id, refreshToken: refresh, expiresAt: DateTime.now().add(expiresIn)).toJson(),
        );

    AuthRepository rolling(MockClientHandler handler, {TokenStore? store}) => AuthRepository(
          authClient: CognitoAuthClient(srpFactory: FakeCognitoSrp.new, rotateRefreshTokens: true, httpClient: srpAwareMockClient(handler)),
          tokenStore: store,
          rollingSession: true,
        );

    test('restore rotates the refresh token even when the ID token is still fresh, restarting the 30 days', () async {
      SharedPreferences.setMockInitialValues({'cognito_tokens': persisted()});
      final repo = rolling((r) async => _tokenResponse(id: 'rotated-id', refresh: 'r2'));

      expect(await _firstRealState(repo), isA<AuthAuthenticated>());

      final prefs = await SharedPreferences.getInstance();
      expect(jsonDecode(prefs.getString('cognito_tokens')!)['refreshToken'], 'r2');
      final expiresAt = await repo.sessionExpiresAt();
      expect(expiresAt!.difference(DateTime.now().add(sessionLifetime)).inMinutes.abs(), lessThan(1));
    });

    test('restore keeps the session when the refresh fails for a network reason', () async {
      SharedPreferences.setMockInitialValues({'cognito_tokens': persisted(id: 'kept-id')});
      final repo = rolling((r) async => throw http.ClientException('offline'));

      final state = await _firstRealState(repo);

      expect(state, isA<AuthAuthenticated>());
      expect((state as AuthAuthenticated).tokens.idToken, 'kept-id');
    });

    test('restore signs out when Cognito rejects the refresh token', () async {
      SharedPreferences.setMockInitialValues({'cognito_tokens': persisted()});
      final repo = rolling((r) async => http.Response(jsonEncode({'__type': 'NotAuthorizedException', 'message': 'Refresh Token has expired'}), 400));

      expect(await _firstRealState(repo), isA<AuthUnauthenticated>());
      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getString('cognito_tokens'), isNull);
    });

    test('renewSession rotates with the stored refresh token, which the background job may have replaced', () async {
      SharedPreferences.setMockInitialValues({'cognito_tokens': persisted()});
      final sent = <String>[];
      final repo = rolling((request) async {
        final body = jsonDecode(request.body) as Map<String, dynamic>;
        sent.add(body['RefreshToken'] as String);
        return _tokenResponse(id: 'id-${sent.length}', refresh: 'r${sent.length + 1}');
      });
      await _firstRealState(repo);
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString('cognito_tokens', persisted(refresh: 'from-background'));

      await repo.renewSession();

      expect(sent, ['r1', 'from-background']);
      expect((repo.state as AuthAuthenticated).tokens.idToken, 'id-2');
    });

    test('a network failure in getValidIdToken surfaces the error but keeps the session', () async {
      SharedPreferences.setMockInitialValues({'cognito_tokens': persisted()});
      var calls = 0;
      final repo = rolling((r) async {
        calls++;
        if (calls == 1) return _tokenResponse(id: 'restored', refresh: 'r2', expiresIn: 30);
        throw http.ClientException('offline');
      });
      await _firstRealState(repo);

      await expectLater(repo.getValidIdToken(), throwsA(isA<http.ClientException>()));

      expect(repo.state, isA<AuthAuthenticated>());
    });

    test('never applies inactivityTtl', () async {
      SharedPreferences.setMockInitialValues({
        'cognito_tokens': persisted(),
        'last_activity_at': DateTime.now().subtract(const Duration(days: 10)).toIso8601String(),
      });
      final repo = rolling((r) async => _tokenResponse(id: 'rotated-id', refresh: 'r2'));

      expect(await _firstRealState(repo), isA<AuthAuthenticated>());
      expect(await repo.getValidIdToken(), 'rotated-id');
    });

    test('reads and writes tokens through the given TokenStore', () async {
      final store = _MemoryTokenStore(persisted());
      final repo = rolling((r) async => _tokenResponse(id: 'rotated-id', refresh: 'r2'), store: store);

      await _firstRealState(repo);

      expect(jsonDecode(store.value!)['refreshToken'], 'r2');
      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getString('cognito_tokens'), isNull);
    });
  });
}

class _MemoryTokenStore implements TokenStore {
  _MemoryTokenStore(this.value);

  String? value;

  @override
  Future<String?> read() async => value;

  @override
  Future<void> write(String tokensJson) async => value = tokensJson;

  @override
  Future<void> delete() async => value = null;
}

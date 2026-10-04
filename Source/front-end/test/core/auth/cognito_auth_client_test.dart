import 'dart:async';
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:front_end/core/auth/cognito_auth_client.dart';

import '../../support/cognito_srp_test_support.dart';

void main() {
  group('CognitoAuthClient.initiateAuth (USER_SRP_AUTH)', () {
    test('answers the PASSWORD_VERIFIER challenge and returns tokens', () async {
      final srp = FakeCognitoSrp();
      final requests = <Map<String, dynamic>>[];
      final client = CognitoAuthClient(
        srpFactory: () => srp,
        httpClient: _cognito(requests, [
          passwordVerifierChallenge(session: 'srp-session'),
          _authenticated(),
        ]),
      );

      final result = await client.initiateAuth(username: 'chamar', password: 'hunter2');

      expect(requests[0]['target'], 'AWSCognitoIdentityProviderService.InitiateAuth');
      expect(requests[0]['AuthFlow'], 'USER_SRP_AUTH');
      expect(requests[0]['AuthParameters'], {'USERNAME': 'chamar', 'SRP_A': 'srp-a'});
      expect(requests[1]['target'], 'AWSCognitoIdentityProviderService.RespondToAuthChallenge');
      expect(requests[1]['ChallengeName'], 'PASSWORD_VERIFIER');
      expect(requests[1]['Session'], 'srp-session');
      expect(requests[1]['ChallengeResponses'], {
        'USERNAME': 'user-id-for-srp',
        'PASSWORD_CLAIM_SECRET_BLOCK': 'secret-block',
        'PASSWORD_CLAIM_SIGNATURE': 'signature',
        'TIMESTAMP': 'timestamp',
      });
      expect(srp.claimedWith, {
        'userIdForSrp': 'user-id-for-srp',
        'password': 'hunter2',
        'saltHex': 'abc123',
        'serverBHex': 'b0b',
        'secretBlock': 'secret-block',
      });
      expect((result as CognitoAuthSuccess).tokens.accessToken, 'access-1');
    });

    test('never sends the password itself', () async {
      final requests = <Map<String, dynamic>>[];
      final client = CognitoAuthClient(
        srpFactory: FakeCognitoSrp.new,
        httpClient: _cognito(requests, [passwordVerifierChallenge(), _authenticated()]),
      );

      await client.initiateAuth(username: 'chamar', password: 'hunter2');

      expect(requests.map(jsonEncode).join(), isNot(contains('hunter2')));
    });

    test('omits Session when the challenge carries none', () async {
      final requests = <Map<String, dynamic>>[];
      final client = CognitoAuthClient(
        srpFactory: FakeCognitoSrp.new,
        httpClient: _cognito(requests, [passwordVerifierChallenge(), _authenticated()]),
      );

      await client.initiateAuth(username: 'chamar', password: 'hunter2');

      expect(requests[1].containsKey('Session'), isFalse);
    });

    test('returns CognitoNewPasswordRequired when Cognito issues that challenge next', () async {
      final client = CognitoAuthClient(
        srpFactory: FakeCognitoSrp.new,
        httpClient: _cognito([], [
          passwordVerifierChallenge(),
          http.Response(jsonEncode({'ChallengeName': 'NEW_PASSWORD_REQUIRED', 'Session': 'session-abc'}), 200),
        ]),
      );

      final result = await client.initiateAuth(username: 'chamar', password: 'temp-pass');

      final challenge = result as CognitoNewPasswordRequired;
      expect(challenge.session, 'session-abc');
      expect(challenge.username, 'user-id-for-srp');
    });

    test('throws CognitoException with the real Cognito error type on a non-200', () async {
      final client = CognitoAuthClient(
        srpFactory: FakeCognitoSrp.new,
        httpClient: _cognito([], [
          passwordVerifierChallenge(),
          http.Response(jsonEncode({'__type': 'NotAuthorizedException', 'message': 'Incorrect username or password.'}), 400),
        ]),
      );

      expect(
        () => client.initiateAuth(username: 'chamar', password: 'wrong'),
        throwsA(isA<CognitoException>().having((e) => e.type, 'type', 'NotAuthorizedException')),
      );
    });

    test('throws when Cognito answers InitiateAuth with any other challenge', () async {
      final client = CognitoAuthClient(
        srpFactory: FakeCognitoSrp.new,
        httpClient: _cognito([], [http.Response(jsonEncode({'ChallengeName': 'SMS_MFA'}), 200)]),
      );

      expect(
        () => client.initiateAuth(username: 'chamar', password: 'hunter2'),
        throwsA(isA<CognitoException>().having((e) => e.type, 'type', 'UnexpectedChallenge')),
      );
    });
  });

  group('CognitoAuthClient.refresh', () {
    test('falls back to the prior refresh token when Cognito omits one', () async {
      final client = CognitoAuthClient(
        httpClient: MockClient((request) async {
          final body = jsonDecode(request.body) as Map<String, dynamic>;
          expect(body['AuthFlow'], 'REFRESH_TOKEN_AUTH');
          return http.Response(
            jsonEncode({
              'AuthenticationResult': {
                'AccessToken': 'access-2',
                'IdToken': 'id-2',
                'ExpiresIn': 3600,
              },
            }),
            200,
          );
        }),
      );

      final tokens = await client.refresh('refresh-original');

      expect(tokens.accessToken, 'access-2');
      expect(tokens.refreshToken, 'refresh-original');
    });

    test('rotates through GetTokensFromRefreshToken for the mobile client', () async {
      final requests = <Map<String, dynamic>>[];
      final client = CognitoAuthClient(
        rotateRefreshTokens: true,
        httpClient: _cognito(requests, [
          http.Response(
            jsonEncode({
              'AuthenticationResult': {'AccessToken': 'access-2', 'IdToken': 'id-2', 'RefreshToken': 'refresh-2', 'ExpiresIn': 3600},
            }),
            200,
          ),
        ]),
      );

      final tokens = await client.refresh('refresh-original');

      expect(requests.single['target'], 'AWSCognitoIdentityProviderService.GetTokensFromRefreshToken');
      expect(requests.single['RefreshToken'], 'refresh-original');
      expect(requests.single.containsKey('AuthFlow'), isFalse);
      expect(tokens.refreshToken, 'refresh-2');
    });
  });

  testWidgets('a Cognito call that never answers times out', (tester) async {
    final client = CognitoAuthClient(httpClient: MockClient((request) => Completer<http.Response>().future));

    Object? error;
    client.initiateAuth(username: 'u', password: 'p').then<void>((_) {}, onError: (Object e) => error = e);
    await tester.pump(const Duration(seconds: 16));

    expect(error, isA<CognitoException>().having((e) => e.type, 'type', 'TimeoutError'));
    expect(error.toString(), startsWith('CognitoException(TimeoutError: '));
  });
}

/// Answers each Cognito call with the next response in order, recording
/// every request body (plus its X-Amz-Target as 'target').
MockClient _cognito(List<Map<String, dynamic>> requests, List<http.Response> responses) {
  var call = 0;
  return MockClient((request) async {
    requests.add({
      ...jsonDecode(request.body) as Map<String, dynamic>,
      'target': request.headers['X-Amz-Target'],
    });
    return responses[call++];
  });
}

http.Response _authenticated() => http.Response(
      jsonEncode({
        'AuthenticationResult': {
          'AccessToken': 'access-1',
          'IdToken': 'id-1',
          'RefreshToken': 'refresh-1',
          'ExpiresIn': 3600,
        },
      }),
      200,
    );

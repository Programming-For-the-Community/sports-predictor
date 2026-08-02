import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:front_end/core/auth/cognito_auth_client.dart';

void main() {
  group('CognitoAuthClient.initiateAuth', () {
    test('returns CognitoAuthSuccess with tokens on a normal login', () async {
      final client = CognitoAuthClient(
        httpClient: MockClient((request) async {
          final body = jsonDecode(request.body) as Map<String, dynamic>;
          expect(body['AuthFlow'], 'USER_PASSWORD_AUTH');
          expect(request.headers['X-Amz-Target'], 'AWSCognitoIdentityProviderService.InitiateAuth');
          return http.Response(
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
        }),
      );

      final result = await client.initiateAuth(username: 'chamar', password: 'hunter2');

      expect(result, isA<CognitoAuthSuccess>());
      final tokens = (result as CognitoAuthSuccess).tokens;
      expect(tokens.accessToken, 'access-1');
      expect(tokens.refreshToken, 'refresh-1');
    });

    test('returns CognitoNewPasswordRequired when Cognito issues that challenge', () async {
      final client = CognitoAuthClient(
        httpClient: MockClient((request) async {
          return http.Response(
            jsonEncode({'ChallengeName': 'NEW_PASSWORD_REQUIRED', 'Session': 'session-abc'}),
            200,
          );
        }),
      );

      final result = await client.initiateAuth(username: 'chamar', password: 'temp-pass');

      expect(result, isA<CognitoNewPasswordRequired>());
      final challenge = result as CognitoNewPasswordRequired;
      expect(challenge.session, 'session-abc');
      expect(challenge.username, 'chamar');
    });

    test('throws CognitoException with the real Cognito error type on a non-200', () async {
      final client = CognitoAuthClient(
        httpClient: MockClient((request) async {
          return http.Response(
            jsonEncode({
              '__type': 'NotAuthorizedException',
              'message': 'Incorrect username or password.',
            }),
            400,
          );
        }),
      );

      expect(
        () => client.initiateAuth(username: 'chamar', password: 'wrong'),
        throwsA(isA<CognitoException>().having((e) => e.type, 'type', 'NotAuthorizedException')),
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
  });
}

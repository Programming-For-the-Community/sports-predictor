import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;

import '../config/app_config.dart';
import 'cognito_srp.dart';

/// Raw HTTP client for the Cognito IDP endpoint -- no third-party SDK.
/// The app client allows only ALLOW_USER_SRP_AUTH and refresh, with no
/// client secret (see Terraform/cognito-app-client.tf): sign-in is an SRP
/// exchange (cognito_srp.dart), so the password itself is never sent.
class CognitoException implements Exception {
  CognitoException(this.type, this.message);

  final String type;
  final String message;

  @override
  String toString() => 'CognitoException($type: $message)';
}

class CognitoTokens {
  CognitoTokens({
    required this.accessToken,
    required this.idToken,
    required this.refreshToken,
    required this.expiresAt,
  });

  factory CognitoTokens.fromAuthenticationResult(Map<String, dynamic> result, {String? fallbackRefreshToken}) {
    final expiresInSeconds = result['ExpiresIn'] as int;
    return CognitoTokens(
      accessToken: result['AccessToken'] as String,
      idToken: result['IdToken'] as String,
      // REFRESH_TOKEN_AUTH's response omits RefreshToken (the same one
      // still applies) -- callers refreshing must supply the prior value.
      refreshToken: (result['RefreshToken'] as String?) ?? fallbackRefreshToken ?? '',
      expiresAt: DateTime.now().add(Duration(seconds: expiresInSeconds)),
    );
  }

  final String accessToken;
  final String idToken;
  final String refreshToken;
  final DateTime expiresAt;

  /// True once within 60s of expiry -- ApiClient refreshes proactively
  /// rather than waiting for a request to fail.
  bool get isNearExpiry => DateTime.now().isAfter(expiresAt.subtract(const Duration(seconds: 60)));

  /// `cognito:username` from the ID token's payload. Read without verifying
  /// the signature -- only for labeling analytics, never for access.
  String? get username {
    try {
      final payload = idToken.split('.')[1];
      final claims = jsonDecode(utf8.decode(base64Url.decode(base64Url.normalize(payload)))) as Map<String, dynamic>;
      return claims['cognito:username'] as String?;
    } catch (_) {
      return null;
    }
  }

  Map<String, dynamic> toJson() => {
        'accessToken': accessToken,
        'idToken': idToken,
        'refreshToken': refreshToken,
        'expiresAt': expiresAt.toIso8601String(),
      };

  factory CognitoTokens.fromJson(Map<String, dynamic> json) => CognitoTokens(
        accessToken: json['accessToken'] as String,
        idToken: json['idToken'] as String,
        refreshToken: json['refreshToken'] as String,
        expiresAt: DateTime.parse(json['expiresAt'] as String),
      );
}

sealed class CognitoAuthResult {}

class CognitoAuthSuccess extends CognitoAuthResult {
  CognitoAuthSuccess(this.tokens);
  final CognitoTokens tokens;
}

/// Admin-created users (see Terraform/cognito-user-pool.tf -- self-signup
/// is disabled, admin_create_user_config only) land in FORCE_CHANGE_PASSWORD
/// status on first login and must set a permanent password before Cognito
/// issues real tokens.
class CognitoNewPasswordRequired extends CognitoAuthResult {
  CognitoNewPasswordRequired(this.session, this.username);
  final String session;
  final String username;
}

class CognitoAuthClient {
  CognitoAuthClient({http.Client? httpClient, CognitoSrp Function()? srpFactory, this.rotateRefreshTokens = false})
      : _httpClient = httpClient ?? http.Client(),
        _srpFactory = srpFactory ?? (() => CognitoSrp(userPoolId: AppConfig.cognitoUserPoolId));

  /// True for the Android app's client (aws_cognito_user_pool_client.mobile),
  /// which has refresh-token rotation on: every refresh returns a new
  /// refresh token with a fresh 30-day lifetime. Rotation only works
  /// through GetTokensFromRefreshToken, not REFRESH_TOKEN_AUTH.
  final bool rotateRefreshTokens;

  static const _timeout = Duration(seconds: 15);

  final http.Client _httpClient;
  final CognitoSrp Function() _srpFactory;

  Uri get _endpoint => Uri.https('cognito-idp.${AppConfig.awsRegion}.amazonaws.com', '/');

  Future<Map<String, dynamic>> _post(String target, Map<String, dynamic> body) async {
    // Without this, a stalled Cognito call hangs getValidIdToken() forever.
    final stopwatch = Stopwatch()..start();
    debugPrint('[CognitoAuthClient] -> POST $target');
    final http.Response response;
    try {
      response = await _httpClient
          .post(
            _endpoint,
            headers: {
              'Content-Type': 'application/x-amz-json-1.1',
              'X-Amz-Target': 'AWSCognitoIdentityProviderService.$target',
            },
            body: jsonEncode(body),
          )
          .timeout(
            _timeout,
            onTimeout: () => throw CognitoException(
              'TimeoutError',
              'Cognito request ($target) timed out after ${_timeout.inSeconds}s',
            ),
          );
    } catch (error) {
      debugPrint('[CognitoAuthClient] <- $target FAILED after ${stopwatch.elapsedMilliseconds}ms: $error');
      rethrow;
    }
    debugPrint('[CognitoAuthClient] <- $target responded ${response.statusCode} in ${stopwatch.elapsedMilliseconds}ms');

    final decoded = jsonDecode(response.body) as Map<String, dynamic>;
    if (response.statusCode != 200) {
      final type = (decoded['__type'] as String?) ?? 'UnknownError';
      throw CognitoException(type, (decoded['message'] as String?) ?? 'Cognito request failed');
    }
    return decoded;
  }

  /// USER_SRP_AUTH: InitiateAuth with SRP_A, then answers Cognito's
  /// PASSWORD_VERIFIER challenge with a signature derived from the password.
  Future<CognitoAuthResult> initiateAuth({required String username, required String password}) async {
    final srp = _srpFactory();
    final challenge = await _post('InitiateAuth', {
      'AuthFlow': 'USER_SRP_AUTH',
      'ClientId': AppConfig.cognitoClientId,
      'AuthParameters': {'USERNAME': username, 'SRP_A': srp.srpA},
    });
    if (challenge['ChallengeName'] != 'PASSWORD_VERIFIER') {
      throw CognitoException('UnexpectedChallenge', 'Expected PASSWORD_VERIFIER, got ${challenge['ChallengeName']}');
    }

    final parameters = challenge['ChallengeParameters'] as Map<String, dynamic>;
    final userIdForSrp = parameters['USER_ID_FOR_SRP'] as String;
    final secretBlock = parameters['SECRET_BLOCK'] as String;
    final claim = srp.passwordClaim(
      userIdForSrp: userIdForSrp,
      password: password,
      saltHex: parameters['SALT'] as String,
      serverBHex: parameters['SRP_B'] as String,
      secretBlock: secretBlock,
    );
    final response = await _post('RespondToAuthChallenge', {
      'ChallengeName': 'PASSWORD_VERIFIER',
      'ClientId': AppConfig.cognitoClientId,
      if (challenge['Session'] != null) 'Session': challenge['Session'],
      'ChallengeResponses': {
        'USERNAME': userIdForSrp,
        'PASSWORD_CLAIM_SECRET_BLOCK': secretBlock,
        'PASSWORD_CLAIM_SIGNATURE': claim.signature,
        'TIMESTAMP': claim.timestamp,
      },
    });
    return _resultFrom(response, username: userIdForSrp);
  }

  Future<CognitoAuthResult> respondToNewPasswordChallenge({
    required String username,
    required String newPassword,
    required String session,
  }) async {
    final response = await _post('RespondToAuthChallenge', {
      'ChallengeName': 'NEW_PASSWORD_REQUIRED',
      'ClientId': AppConfig.cognitoClientId,
      'Session': session,
      'ChallengeResponses': {'USERNAME': username, 'NEW_PASSWORD': newPassword},
    });
    return _resultFrom(response, username: username);
  }

  Future<CognitoTokens> refresh(String refreshToken) async {
    if (rotateRefreshTokens) {
      final response = await _post('GetTokensFromRefreshToken', {
        'ClientId': AppConfig.cognitoClientId,
        'RefreshToken': refreshToken,
      });
      final result = response['AuthenticationResult'] as Map<String, dynamic>;
      return CognitoTokens.fromAuthenticationResult(result, fallbackRefreshToken: refreshToken);
    }
    final response = await _post('InitiateAuth', {
      'AuthFlow': 'REFRESH_TOKEN_AUTH',
      'ClientId': AppConfig.cognitoClientId,
      'AuthParameters': {'REFRESH_TOKEN': refreshToken},
    });
    final result = response['AuthenticationResult'] as Map<String, dynamic>;
    return CognitoTokens.fromAuthenticationResult(result, fallbackRefreshToken: refreshToken);
  }

  CognitoAuthResult _resultFrom(Map<String, dynamic> response, {required String username}) {
    final challengeName = response['ChallengeName'] as String?;
    if (challengeName == 'NEW_PASSWORD_REQUIRED') {
      return CognitoNewPasswordRequired(response['Session'] as String, username);
    }
    final result = response['AuthenticationResult'] as Map<String, dynamic>;
    return CognitoAuthSuccess(CognitoTokens.fromAuthenticationResult(result));
  }
}

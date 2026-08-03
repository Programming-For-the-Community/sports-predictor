import 'dart:convert';

import 'package:http/http.dart' as http;

import '../config/app_config.dart';

/// Raw HTTP client for the Cognito IDP endpoint -- no third-party SDK.
/// The app client uses ALLOW_USER_PASSWORD_AUTH with no client secret (see
/// Terraform/cognito-app-client.tf), so the entire auth surface is two JSON
/// POSTs; pulling in a full SDK for that would trade a small amount of
/// boilerplate for a real dependency-freshness risk.
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
  CognitoAuthClient({http.Client? httpClient}) : _httpClient = httpClient ?? http.Client();

  static const _timeout = Duration(seconds: 15);

  final http.Client _httpClient;

  Uri get _endpoint => Uri.https('cognito-idp.${AppConfig.awsRegion}.amazonaws.com', '/');

  Future<Map<String, dynamic>> _post(String target, Map<String, dynamic> body) async {
    // Same reasoning as ApiClient._send's timeout -- without this, a
    // stalled Cognito call hangs getValidAccessToken() forever, which hangs
    // every ApiClient.get() call before it ever reaches ApiClient's own
    // timeout-guarded request.
    final response = await _httpClient
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

    final decoded = jsonDecode(response.body) as Map<String, dynamic>;
    if (response.statusCode != 200) {
      final type = (decoded['__type'] as String?) ?? 'UnknownError';
      throw CognitoException(type, (decoded['message'] as String?) ?? 'Cognito request failed');
    }
    return decoded;
  }

  Future<CognitoAuthResult> initiateAuth({required String username, required String password}) async {
    final response = await _post('InitiateAuth', {
      'AuthFlow': 'USER_PASSWORD_AUTH',
      'ClientId': AppConfig.cognitoClientId,
      'AuthParameters': {'USERNAME': username, 'PASSWORD': password},
    });
    return _resultFrom(response, username: username);
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

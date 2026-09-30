import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:front_end/core/auth/cognito_srp.dart';

/// Stands in for the SRP math (covered by cognito_srp_test.dart): a fixed
/// SRP_A, and a fixed claim recording what it was asked to sign.
class FakeCognitoSrp implements CognitoSrp {
  Map<String, String>? claimedWith;

  @override
  String get srpA => 'srp-a';

  @override
  PasswordClaim passwordClaim({
    required String userIdForSrp,
    required String password,
    required String saltHex,
    required String serverBHex,
    required String secretBlock,
  }) {
    claimedWith = {
      'userIdForSrp': userIdForSrp,
      'password': password,
      'saltHex': saltHex,
      'serverBHex': serverBHex,
      'secretBlock': secretBlock,
    };
    return const PasswordClaim(signature: 'signature', timestamp: 'timestamp');
  }
}

/// Cognito's answer to USER_SRP_AUTH: the PASSWORD_VERIFIER challenge.
http.Response passwordVerifierChallenge({String? session}) => http.Response(
      jsonEncode({
        'ChallengeName': 'PASSWORD_VERIFIER',
        if (session != null) 'Session': session,
        'ChallengeParameters': {
          'USER_ID_FOR_SRP': 'user-id-for-srp',
          'SALT': 'abc123',
          'SRP_B': 'b0b',
          'SECRET_BLOCK': 'secret-block',
        },
      }),
      200,
    );

/// A MockClient that answers the USER_SRP_AUTH InitiateAuth with the
/// PASSWORD_VERIFIER challenge and hands every other call to [handler].
MockClient srpAwareMockClient(MockClientHandler handler) => MockClient((request) async {
      final body = jsonDecode(request.body) as Map<String, dynamic>;
      return body['AuthFlow'] == 'USER_SRP_AUTH' ? passwordVerifierChallenge() : handler(request);
    });

import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/auth/cognito_srp.dart';

// Reference values from pycognito 2024.5.1 (an independent implementation of
// Cognito's SRP), for fixed inputs: pool us-east-2_AbCdEf123, user "chamar",
// the private value below, and a fixed salt, SRP_B, SECRET_BLOCK and clock.
const _poolId = 'us-east-2_AbCdEf123';
const _username = 'chamar';
const _password = 'correct horse battery staple';
const _salt = '8b3c9d2e1f0a4b5c6d7e8f901a2b3c4d';
const _secretBlock = 'AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8gISIjJCUmJygpKissLS4vMDEyMzQ1Njc4OTo7PD0+Pw==';
const _smallA = '7f1c3a9e5b2d4c6f8a0b1c2d3e4f5061728394a5b6c7d8e9f0a1b2c3d4e5f607';
const _expectedLargeA =
    'ee9d992b3a8702a63c92f9c615942f6f75cad23924683ed1405a43ed0b3df0639811fc26d8918df7befc55efc74fee4a3ec5'
    'a463242705f2a5c61cfcb9ec40e812f2b00ee8c970ff586d3476967defdf108dc8531f3faa5cc9ed7eeeaaf8e5f01356efec'
    'dba8b5a0c72ed73cc06f4656b42e202244584f6e251e3bb15d6c5158e08bebf54ec5d6c5b1122ca478c9d40f4542927b024b'
    'a6cd31ab45311dd278b64b142526211bd1178c04a4346354c351e76227243679d5f3ea0c2c1f777f0858ebde55a0e13b4b85'
    '3b4abaadcaba57ee19cf7031c16eedb3d3db74f2560344d68095388b4ca6f9d62c96d51739d77b26194138d35d7235910752'
    '7f5fee28a7c453720f6b6c6fa60f588244bfbf18e0c7c18aace4fe31111c16742d3f44d8d64550e780c665f5d1489daed844'
    'ee3aae4faf6c82770b18a3e011965aa82743c213b95c7316a61b4143c1ec9108e3160b20f01a3a93cecbe58888afd76e5977'
    'b9c416e1fb1c77745616b27474cccd10118427d7479a596ddb2ae84f09ac76c79dbc';
const _serverB =
    '72763f1db9ed9ca695d9479af81531f9cee603f3d5e27cf9437ede782855c4c3efe9ae016974f36a46dffce5c72b21c0fcd9'
    '726b8b3d829fdff8112ca9d6bc7b89c52c80d1e7f2713c1888c68b43d46c22c6257a6ab441cb78a7ef235620e707ce32bbb9'
    '1c29cb57be52d4de173b41f5504fc40874576be271ad8e8e269c0a2e3c601b68a52cd32f7e50ff86a9c764e8afbd052fe29d'
    'fe953de029370f78879b187145e5cddadb0bf95f2ed3cd2e37a3bd1f706c83e002d652dea11f8768bf250a4cf6e338e99589'
    'a22b7a0f2b1c7039044be729caab0d4af4c55201ebfa64694dd75b5cf9e82c582c038214ada8638820e5787c5dba74e81d39'
    'd54f60167a1dfdb09888eefd9db206a32682842a44f8b2f6452f616b0a84f189fb0bda4e96dbf0347f17aed6227a549577d4'
    '98886ba525be77bdfebff65bc880e7bdf7c5a66dcc42f05412efd1fdac2eec9144f69f7b3978188caa03c4628606ce8a2424'
    'c65a16b73042bc5db262fa4a9a167e2eb59e97dfa2bd7f4d8e7fa801a6c2ae5d85c7';
const _expectedTimestamp = 'Thu Sep 3 07:05:09 UTC 2026';
const _expectedSignature = 'a7rvATfQWgyW2fUID43D1mNQe1mkgKYW8vsNtx/P/Ks=';

CognitoSrp _srp() => CognitoSrp(
      userPoolId: _poolId,
      smallA: BigInt.parse(_smallA, radix: 16),
      clock: () => DateTime.utc(2026, 9, 3, 7, 5, 9),
    );

PasswordClaim _claim({String password = _password, String serverB = _serverB}) => _srp().passwordClaim(
      userIdForSrp: _username,
      password: password,
      saltHex: _salt,
      serverBHex: serverB,
      secretBlock: _secretBlock,
    );

void main() {
  group('CognitoSrp', () {
    test('derives SRP_A from the private value', () {
      expect(_srp().srpA, _expectedLargeA);
    });

    test('signs the PASSWORD_VERIFIER claim exactly as the reference implementation', () {
      final claim = _claim();

      expect(claim.timestamp, _expectedTimestamp);
      expect(claim.signature, _expectedSignature);
    });

    test('a different password produces a different signature', () {
      expect(_claim(password: 'wrong password').signature, isNot(_expectedSignature));
    });

    test('rejects an SRP_B that is a multiple of N', () {
      expect(() => _claim(serverB: '0'), throwsArgumentError);
    });

    test('generates a fresh private value per instance by default', () {
      expect(CognitoSrp(userPoolId: _poolId).srpA, isNot(CognitoSrp(userPoolId: _poolId).srpA));
    });
  });

  group('cognitoTimestamp', () {
    test('leaves the day unpadded and zero-pads the time', () {
      expect(cognitoTimestamp(DateTime.utc(2026, 1, 5, 3, 4, 5)), 'Mon Jan 5 03:04:05 UTC 2026');
    });

    test('formats in UTC whatever the input zone', () {
      expect(cognitoTimestamp(DateTime.utc(2026, 12, 31, 23, 59, 59).toLocal()), 'Thu Dec 31 23:59:59 UTC 2026');
    });
  });
}

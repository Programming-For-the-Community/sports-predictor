import 'dart:convert';
import 'dart:math';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';

/// Cognito's Secure Remote Password flow (USER_SRP_AUTH): the password
/// never leaves the device -- Cognito receives only the public value A and
/// a signature proving knowledge of the password. SRP-6a over the 3072-bit
/// RFC 5054 group with g = 2, SHA-256 and an HKDF key ("Caldera Derived
/// Key"), matching Cognito's own client SDKs.
class CognitoSrp {
  CognitoSrp({required String userPoolId, Random? random, BigInt? smallA, DateTime Function()? clock})
      : _poolName = userPoolId.split('_').last,
        _smallA = smallA ?? _randomBigInt(random ?? Random.secure(), _smallABytes),
        _clock = clock ?? DateTime.now {
    _largeA = _g.modPow(_smallA, _n);
  }

  // 256-bit private exponent: ~128-bit security in the 3072-bit group.
  static const _smallABytes = 32;

  static final BigInt _n = BigInt.parse(
    'FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74'
    '020BBEA63B139B22514A08798E3404DDEF9519B3CD3A431B302B0A6DF25F1437'
    '4FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED'
    'EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF05'
    '98DA48361C55D39A69163FA8FD24CF5F83655D23DCA3AD961C62F356208552BB'
    '9ED529077096966D670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B'
    'E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF695581718'
    '3995497CEA956AE515D2261898FA051015728E5A8AAAC42DAD33170D04507A33'
    'A85521ABDF1CBA64ECFB850458DBEF0A8AEA71575D060C7DB3970F85A6E1E4C7'
    'ABF5AE8CDB0933D71E8C94E04A25619DCEE3D2261AD2EE6BF12FFA06D98A0864'
    'D87602733EC86A64521F2B18177B200CBBE117577A615D6C770988C0BAD946E2'
    '08E24FA074E5AB3143DB5BFCE0FD108E4B82D120A93AD2CAFFFFFFFFFFFFFFFF',
    radix: 16,
  );
  static final BigInt _g = BigInt.two;
  static final BigInt _k = _hexToBigInt(_hexHash(_padHex(_n) + _padHex(_g)));
  static final List<int> _hkdfInfo = [...utf8.encode('Caldera Derived Key'), 1];

  final String _poolName;
  final BigInt _smallA;
  final DateTime Function() _clock;
  late final BigInt _largeA;

  /// The SRP_A auth parameter for InitiateAuth.
  String get srpA => _largeA.toRadixString(16);

  /// The PASSWORD_VERIFIER challenge response, from Cognito's challenge
  /// parameters (USER_ID_FOR_SRP, SALT, SRP_B, SECRET_BLOCK).
  PasswordClaim passwordClaim({
    required String userIdForSrp,
    required String password,
    required String saltHex,
    required String serverBHex,
    required String secretBlock,
  }) {
    final serverB = BigInt.parse(serverBHex, radix: 16);
    final u = _hexToBigInt(_hexHash(_padHex(_largeA) + _padHex(serverB)));
    if (serverB % _n == BigInt.zero || u == BigInt.zero) {
      throw ArgumentError('Cognito returned an invalid SRP_B');
    }

    final usernamePasswordHash = sha256.convert(utf8.encode('$_poolName$userIdForSrp:$password')).toString();
    final x = _hexToBigInt(_hexHash(_padHex(BigInt.parse(saltHex, radix: 16)) + usernamePasswordHash));
    final s = ((serverB - _k * _g.modPow(x, _n)) % _n).modPow(_smallA + u * x, _n);
    final key = _hkdf(ikm: _hexToBytes(_padHex(s)), salt: _hexToBytes(_padHex(u)));

    final timestamp = cognitoTimestamp(_clock());
    final message = [
      ...utf8.encode(_poolName),
      ...utf8.encode(userIdForSrp),
      ...base64.decode(secretBlock),
      ...utf8.encode(timestamp),
    ];
    return PasswordClaim(
      signature: base64.encode(Hmac(sha256, key).convert(message).bytes),
      timestamp: timestamp,
    );
  }

  static List<int> _hkdf({required List<int> ikm, required List<int> salt}) {
    final prk = Hmac(sha256, salt).convert(ikm).bytes;
    return Hmac(sha256, prk).convert(_hkdfInfo).bytes.sublist(0, 16);
  }

  static BigInt _randomBigInt(Random random, int bytes) =>
      _hexToBigInt(List.generate(bytes, (_) => random.nextInt(256).toRadixString(16).padLeft(2, '0')).join());

  static BigInt _hexToBigInt(String hex) => BigInt.parse(hex, radix: 16);

  /// SHA-256 of the bytes a hex string encodes, as 64 hex digits.
  static String _hexHash(String hex) => sha256.convert(_hexToBytes(hex)).toString();

  static Uint8List _hexToBytes(String hex) =>
      Uint8List.fromList([for (var i = 0; i < hex.length; i += 2) int.parse(hex.substring(i, i + 2), radix: 16)]);

  /// Even-length hex, with a leading 00 byte when the high bit is set so the
  /// value reads as positive -- the same encoding Cognito hashes.
  static String _padHex(BigInt value) {
    var hex = value.toRadixString(16);
    if (hex.length.isOdd) hex = '0$hex';
    return '89abcdef'.contains(hex[0]) ? '00$hex' : hex;
  }
}

class PasswordClaim {
  const PasswordClaim({required this.signature, required this.timestamp});

  final String signature;
  final String timestamp;
}

const _weekdays = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const _months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/// Cognito's TIMESTAMP format, in UTC: "Thu Sep 3 07:05:09 UTC 2026" --
/// unpadded day of month, zero-padded time.
String cognitoTimestamp(DateTime time) {
  final utc = time.toUtc();
  String two(int value) => value.toString().padLeft(2, '0');
  return '${_weekdays[utc.weekday - 1]} ${_months[utc.month - 1]} ${utc.day} '
      '${two(utc.hour)}:${two(utc.minute)}:${two(utc.second)} UTC ${utc.year}';
}

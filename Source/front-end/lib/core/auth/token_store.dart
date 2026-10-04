import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Where the serialized CognitoTokens JSON is persisted.
abstract interface class TokenStore {
  Future<String?> read();
  Future<void> write(String tokensJson);
  Future<void> delete();
}

const tokensStorageKey = 'cognito_tokens';

/// Web: the browser's localStorage, via SharedPreferences.
class PrefsTokenStore implements TokenStore {
  PrefsTokenStore(this._prefs);

  final SharedPreferences _prefs;

  @override
  Future<String?> read() async => _prefs.getString(tokensStorageKey);

  @override
  Future<void> write(String tokensJson) => _prefs.setString(tokensStorageKey, tokensJson);

  @override
  Future<void> delete() => _prefs.remove(tokensStorageKey);
}

/// Android app: Keystore-backed storage, shared with the background
/// WorkManager isolate (background_checks.dart).
class SecureTokenStore implements TokenStore {
  SecureTokenStore([FlutterSecureStorage? storage]) : _storage = storage ?? const FlutterSecureStorage();

  final FlutterSecureStorage _storage;

  @override
  Future<String?> read() => _storage.read(key: tokensStorageKey);

  @override
  Future<void> write(String tokensJson) => _storage.write(key: tokensStorageKey, value: tokensJson);

  @override
  Future<void> delete() => _storage.delete(key: tokensStorageKey);
}

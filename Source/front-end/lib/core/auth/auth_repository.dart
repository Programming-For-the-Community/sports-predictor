import 'dart:async';
import 'dart:convert';

import 'package:flutter_riverpod/legacy.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'cognito_auth_client.dart';

const _prefsKey = 'cognito_tokens';
const _lastActivityPrefsKey = 'last_activity_at';

/// How long the app tolerates zero authenticated activity before forcing
/// a fresh login, independent of whether the refresh token itself is
/// still valid.
const inactivityTtl = Duration(minutes: 30);

/// How often recordActivity actually writes -- see its own docstring.
const _recordActivityThrottle = Duration(seconds: 30);

sealed class AuthState {}

/// App just started -- restoring a persisted session hasn't finished yet.
/// go_router's redirect treats this the same as unauthenticated (no route
/// decision should be made off a stale/incomplete state).
class AuthInitial extends AuthState {}

class AuthUnauthenticated extends AuthState {}

/// Admin-created users land here on first login.
class AuthNeedsNewPassword extends AuthState {
  AuthNeedsNewPassword(this.session, this.username);
  final String session;
  final String username;
}

class AuthAuthenticated extends AuthState {
  AuthAuthenticated(this.tokens);
  final CognitoTokens tokens;
}

class AuthRepository extends StateNotifier<AuthState> {
  AuthRepository({CognitoAuthClient? authClient, SharedPreferences? prefs})
      : _authClient = authClient ?? CognitoAuthClient(),
        _prefs = prefs,
        super(AuthInitial()) {
    _restoreSession();
  }

  final CognitoAuthClient _authClient;
  SharedPreferences? _prefs;

  // Resolves once _restoreSession's very first pass finishes (regardless of
  // outcome). getValidIdToken awaits this before ever reading `state` --
  // without it, a page that builds and fetches immediately on a browser
  // refresh (go_router stays on the already-deep-linked URL while state is
  // AuthInitial, it doesn't wait) would call getValidIdToken while restore
  // is still reading SharedPreferences/refreshing a token, throwing "No
  // authenticated session" even for a genuinely still-valid session.
  final Completer<void> _restored = Completer<void>();

  Future<SharedPreferences> get _prefsInstance async => _prefs ??= await SharedPreferences.getInstance();

  Future<void> _restoreSession() async {
    final prefs = await _prefsInstance;
    final raw = prefs.getString(_prefsKey);
    if (raw == null) {
      state = AuthUnauthenticated();
      _restored.complete();
      return;
    }

    // Checked before even attempting a refresh -- no point refreshing a
    // session inactivityTtl is about to invalidate anyway.
    if (await _isInactive()) {
      await _clear();
      state = AuthUnauthenticated();
      _restored.complete();
      return;
    }

    var tokens = CognitoTokens.fromJson(jsonDecode(raw) as Map<String, dynamic>);
    if (tokens.isNearExpiry) {
      try {
        tokens = await _authClient.refresh(tokens.refreshToken);
        await _persist(tokens);
      } catch (_) {
        await _clear();
        state = AuthUnauthenticated();
        _restored.complete();
        return;
      }
    }
    await _touchActivity();
    state = AuthAuthenticated(tokens);
    _restored.complete();
  }

  Future<void> login({required String username, required String password}) async {
    final result = await _authClient.initiateAuth(username: username, password: password);
    await _applyResult(result);
  }

  Future<void> respondToNewPassword(String newPassword) async {
    final current = state;
    if (current is! AuthNeedsNewPassword) {
      throw StateError('respondToNewPassword called outside the NEW_PASSWORD_REQUIRED challenge');
    }
    final result = await _authClient.respondToNewPasswordChallenge(
      username: current.username,
      newPassword: newPassword,
      session: current.session,
    );
    await _applyResult(result);
  }

  Future<void> _applyResult(CognitoAuthResult result) async {
    switch (result) {
      case CognitoAuthSuccess(:final tokens):
        await _persist(tokens);
        await _touchActivity();
        state = AuthAuthenticated(tokens);
      case CognitoNewPasswordRequired(:final session, :final username):
        state = AuthNeedsNewPassword(session, username);
    }
  }

  /// Called by ApiClient before every request -- refreshes proactively
  /// within ~60s of expiry rather than waiting for a 401. If the refresh
  /// token itself has expired or been revoked, transitions to
  /// AuthUnauthenticated instead of leaving the caller to surface a raw
  /// exception; app_router's redirect listens to this state and bounces to
  /// /login as soon as it changes.
  ///
  /// Returns the ID token, not the access token -- API Gateway's
  /// COGNITO_USER_POOLS authorizer expects an ID token for methods with no
  /// authorization_scopes configured.
  ///
  /// forceRefresh skips the isNearExpiry check -- ApiClient's 401-retry
  /// path needs this: a token can be rejected server-side while still
  /// looking fresh by its own local expiresAt.
  Future<String> getValidIdToken({bool forceRefresh = false}) async {
    await _restored.future;
    final current = state;
    if (current is! AuthAuthenticated) {
      throw StateError('No authenticated session');
    }
    // Checked before the normal near-expiry refresh below so a session
    // that's merely idle-too-long gets bounced to a fresh login even when
    // its token technically still has time left on the clock.
    if (await _isInactive()) {
      await _clear();
      state = AuthUnauthenticated();
      throw StateError('Session expired after $inactivityTtl of inactivity');
    }
    await _touchActivity();
    if (!forceRefresh && !current.tokens.isNearExpiry) {
      return current.tokens.idToken;
    }
    try {
      final refreshed = await _authClient.refresh(current.tokens.refreshToken);
      await _persist(refreshed);
      state = AuthAuthenticated(refreshed);
      return refreshed.idToken;
    } catch (_) {
      await _clear();
      state = AuthUnauthenticated();
      rethrow;
    }
  }

  Future<void> logout() async {
    await _clear();
    state = AuthUnauthenticated();
  }

  Future<void> _persist(CognitoTokens tokens) async {
    final prefs = await _prefsInstance;
    await prefs.setString(_prefsKey, jsonEncode(tokens.toJson()));
  }

  Future<void> _clear() async {
    final prefs = await _prefsInstance;
    await prefs.remove(_prefsKey);
    await prefs.remove(_lastActivityPrefsKey);
  }

  Future<void> _touchActivity() async {
    final prefs = await _prefsInstance;
    await prefs.setString(_lastActivityPrefsKey, DateTime.now().toIso8601String());
  }

  /// Called by app.dart's app-shell pointer listener on any real user
  /// interaction (tap, drag, scroll) -- makes inactivityTtl track actual
  /// usage instead of just API-call cadence.
  ///
  /// Throttled to once per _recordActivityThrottle, checked against the
  /// same persisted timestamp _isInactive reads, since a drag/scroll
  /// gesture fires pointer-move events many times a second. No-ops while
  /// unauthenticated.
  Future<void> recordActivity() async {
    if (state is! AuthAuthenticated) return;
    final prefs = await _prefsInstance;
    final raw = prefs.getString(_lastActivityPrefsKey);
    if (raw != null && DateTime.now().difference(DateTime.parse(raw)) < _recordActivityThrottle) return;
    await _touchActivity();
  }

  /// False when no activity has ever been recorded -- treated as active
  /// rather than instantly expiring the session.
  Future<bool> _isInactive() async {
    final prefs = await _prefsInstance;
    final raw = prefs.getString(_lastActivityPrefsKey);
    if (raw == null) return false;
    final lastActivity = DateTime.parse(raw);
    return DateTime.now().isAfter(lastActivity.add(inactivityTtl));
  }
}

final authRepositoryProvider = StateNotifierProvider<AuthRepository, AuthState>((ref) {
  return AuthRepository();
});

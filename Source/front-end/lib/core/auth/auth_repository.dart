import 'dart:convert';

import 'package:flutter_riverpod/legacy.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'cognito_auth_client.dart';

const _prefsKey = 'cognito_tokens';

sealed class AuthState {}

/// App just started -- restoring a persisted session hasn't finished yet.
/// go_router's redirect treats this the same as unauthenticated (no route
/// decision should be made off a stale/incomplete state).
class AuthInitial extends AuthState {}

class AuthUnauthenticated extends AuthState {}

/// Admin-created users land here on first login -- see
/// CognitoNewPasswordRequired's own doc comment.
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

  Future<SharedPreferences> get _prefsInstance async => _prefs ??= await SharedPreferences.getInstance();

  Future<void> _restoreSession() async {
    final prefs = await _prefsInstance;
    final raw = prefs.getString(_prefsKey);
    if (raw == null) {
      state = AuthUnauthenticated();
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
        return;
      }
    }
    state = AuthAuthenticated(tokens);
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
        state = AuthAuthenticated(tokens);
      case CognitoNewPasswordRequired(:final session, :final username):
        state = AuthNeedsNewPassword(session, username);
    }
  }

  /// Called by ApiClient before every request -- refreshes proactively
  /// within ~60s of expiry rather than waiting for a 401. If the refresh
  /// token itself has expired or been revoked, transitions to
  /// AuthUnauthenticated (same as _restoreSession's own failure path)
  /// instead of leaving the caller to surface a raw exception on whatever
  /// page happened to trigger it -- app_router's redirect already listens
  /// to this state and bounces to /login as soon as it changes.
  ///
  /// forceRefresh skips the isNearExpiry check -- ApiClient's 401-retry
  /// path needs this: a token can be rejected server-side (revoked, clock
  /// skew, whatever) while still looking fresh by its own local expiresAt,
  /// and resending that same not-actually-valid token on "retry" would
  /// just fail identically.
  Future<String> getValidAccessToken({bool forceRefresh = false}) async {
    final current = state;
    if (current is! AuthAuthenticated) {
      throw StateError('No authenticated session');
    }
    if (!forceRefresh && !current.tokens.isNearExpiry) {
      return current.tokens.accessToken;
    }
    try {
      final refreshed = await _authClient.refresh(current.tokens.refreshToken);
      await _persist(refreshed);
      state = AuthAuthenticated(refreshed);
      return refreshed.accessToken;
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
  }
}

final authRepositoryProvider = StateNotifierProvider<AuthRepository, AuthState>((ref) {
  return AuthRepository();
});

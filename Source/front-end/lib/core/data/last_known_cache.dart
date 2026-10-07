import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// The last successful JSON response per key, kept on the device so a
/// page opened again after a reload or app restart has something to show
/// while the fresh response is on its way. See [lastKnownThenFresh].
class LastKnownStore {
  static const _prefix = 'last_known.';

  // Older than this and a saved response isn't shown at all.
  static const maxAge = Duration(hours: 24);

  // Web's localStorage is ~5MB for the whole origin; a response larger
  // than this is simply not saved.
  static const maxEntryChars = 300000;

  Future<Object?> read(String key) async {
    try {
      final raw = (await SharedPreferences.getInstance()).getString('$_prefix$key');
      if (raw == null) return null;
      final entry = jsonDecode(raw) as Map<String, dynamic>;
      final savedAt = DateTime.fromMillisecondsSinceEpoch(entry['saved_at'] as int);
      if (DateTime.now().difference(savedAt) > maxAge) return null;
      return entry['body'];
    } catch (error) {
      debugPrint('[LastKnownStore] read $key failed: $error');
      return null;
    }
  }

  Future<void> write(String key, Object? body) async {
    try {
      final raw = jsonEncode({'saved_at': DateTime.now().millisecondsSinceEpoch, 'body': body});
      if (raw.length > maxEntryChars) return;
      final prefs = await SharedPreferences.getInstance();
      if (!await prefs.setString('$_prefix$key', raw)) {
        // Out of room -- drop everything saved so far and keep this one.
        await clearLastKnown(prefs);
        await prefs.setString('$_prefix$key', raw);
      }
    } catch (error) {
      debugPrint('[LastKnownStore] write $key failed: $error');
    }
  }

  /// Removes every saved response. Called on sign-out.
  static Future<void> clearLastKnown(SharedPreferences prefs) async {
    for (final key in prefs.getKeys().where((k) => k.startsWith(_prefix)).toList()) {
      await prefs.remove(key);
    }
  }
}

final lastKnownStoreProvider = Provider<LastKnownStore>((ref) => LastKnownStore());

class _LastKnownSession {
  // Keys already loaded once since the app started -- a saved response is
  // only ever shown in place of the first load.
  final Set<String> loaded = {};
  // A fresh response waiting for its provider's rebuild to pick it up.
  final Map<String, Object?> fresh = {};
}

final _lastKnownSessionProvider = Provider<_LastKnownSession>((ref) => _LastKnownSession());

/// For a FutureProvider's body. The first load of [key] since the app
/// started returns the response saved on this device, if there is one,
/// and fetches the fresh one in the background -- the provider then
/// rebuilds itself with it. Every later load (a poll, a pull-to-refresh)
/// goes straight to [fetch].
///
/// [fetch] returns the decoded JSON body; [parse] turns it into the
/// provider's value and may throw (e.g. a "computing" response), in which
/// case the body isn't saved.
Future<T> lastKnownThenFresh<T>(
  Ref ref, {
  required String key,
  required Future<Object?> Function() fetch,
  required T Function(Object? json) parse,
}) async {
  final session = ref.read(_lastKnownSessionProvider);
  if (session.fresh.containsKey(key)) return parse(session.fresh.remove(key));

  final store = ref.read(lastKnownStoreProvider);
  if (session.loaded.add(key)) {
    final saved = await store.read(key);
    final lastKnown = saved == null ? null : _tryParse(parse, saved);
    if (lastKnown != null) {
      unawaited(_refreshInBackground(ref, session, store, key, fetch, parse));
      return lastKnown;
    }
  }

  final body = await fetch();
  final value = parse(body);
  await store.write(key, body);
  return value;
}

T? _tryParse<T>(T Function(Object? json) parse, Object? body) {
  try {
    return parse(body);
  } catch (_) {
    return null;
  }
}

Future<void> _refreshInBackground<T>(
  Ref ref,
  _LastKnownSession session,
  LastKnownStore store,
  String key,
  Future<Object?> Function() fetch,
  T Function(Object? json) parse,
) async {
  final Object? body;
  try {
    body = await fetch();
  } catch (error) {
    // The saved response stays on screen; the next refresh reports the error.
    debugPrint('[lastKnownThenFresh] background refresh of $key failed: $error');
    return;
  }
  if (_tryParse(parse, body) != null) await store.write(key, body);
  // Rebuilt in the meantime -- that build fetched for itself.
  if (!ref.mounted) return;
  session.fresh[key] = body;
  ref.invalidateSelf();
}

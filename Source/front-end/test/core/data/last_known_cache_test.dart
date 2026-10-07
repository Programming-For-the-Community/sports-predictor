import 'dart:async';
import 'dart:convert';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:front_end/core/data/last_known_cache.dart';

String _saved(Object? body, {Duration age = Duration.zero}) =>
    jsonEncode({'saved_at': DateTime.now().subtract(age).millisecondsSinceEpoch, 'body': body});

/// A provider whose fetch the test controls, built the way the real
/// events/prediction providers use lastKnownThenFresh.
FutureProvider<int> _provider(Future<Object?> Function() fetch, {int Function(Object?)? parse}) {
  return FutureProvider<int>((ref) {
    return lastKnownThenFresh(ref, key: 'k', fetch: fetch, parse: parse ?? (json) => (json as Map<String, dynamic>)['n'] as int);
  });
}

void main() {
  group('LastKnownStore', () {
    test('reads back what it wrote', () async {
      SharedPreferences.setMockInitialValues({});
      final store = LastKnownStore();

      await store.write('k', {'n': 1});

      expect(await store.read('k'), {'n': 1});
    });

    test('nothing saved reads as null', () async {
      SharedPreferences.setMockInitialValues({});

      expect(await LastKnownStore().read('k'), isNull);
    });

    test('a response older than maxAge is not returned', () async {
      SharedPreferences.setMockInitialValues({
        'last_known.k': _saved({'n': 1}, age: LastKnownStore.maxAge + const Duration(minutes: 1)),
      });

      expect(await LastKnownStore().read('k'), isNull);
    });

    test('a corrupt entry reads as null instead of throwing', () async {
      SharedPreferences.setMockInitialValues({'last_known.k': 'not json'});

      expect(await LastKnownStore().read('k'), isNull);
    });

    test('an oversized response is not saved', () async {
      SharedPreferences.setMockInitialValues({});
      final store = LastKnownStore();

      await store.write('k', {'blob': 'x' * (LastKnownStore.maxEntryChars + 1)});

      expect(await store.read('k'), isNull);
    });

    test('clearLastKnown removes saved responses and nothing else', () async {
      SharedPreferences.setMockInitialValues({'last_known.k': _saved({'n': 1}), 'other': 'kept'});
      final prefs = await SharedPreferences.getInstance();

      await LastKnownStore.clearLastKnown(prefs);

      expect(prefs.getString('last_known.k'), isNull);
      expect(prefs.getString('other'), 'kept');
    });
  });

  group('lastKnownThenFresh', () {
    test('with nothing saved, waits for the fetch and saves it', () async {
      SharedPreferences.setMockInitialValues({});
      final container = ProviderContainer();
      addTearDown(container.dispose);
      final provider = _provider(() async => {'n': 7});

      expect(await container.read(provider.future), 7);
      expect(await LastKnownStore().read('k'), {'n': 7});
    });

    test('shows the saved response first, then rebuilds with the fresh one', () async {
      SharedPreferences.setMockInitialValues({'last_known.k': _saved({'n': 1})});
      final container = ProviderContainer();
      addTearDown(container.dispose);
      final fetched = Completer<Object?>();
      final provider = _provider(() => fetched.future);
      final seen = <int>[];
      container.listen(provider, (_, next) {
        if (next.hasValue) seen.add(next.requireValue);
      }, fireImmediately: true);

      expect(await container.read(provider.future), 1);

      fetched.complete({'n': 2});
      await pumpEventQueue();

      expect(container.read(provider).requireValue, 2);
      expect(seen, containsAllInOrder([1, 2]));
      expect(await LastKnownStore().read('k'), {'n': 2});
    });

    test('a later refresh goes straight to the fetch, not back to the saved response', () async {
      SharedPreferences.setMockInitialValues({'last_known.k': _saved({'n': 1})});
      final container = ProviderContainer();
      addTearDown(container.dispose);
      var n = 1;
      final provider = _provider(() async => {'n': ++n});
      container.listen(provider, (_, __) {});

      await container.read(provider.future);
      await pumpEventQueue();
      expect(container.read(provider).requireValue, 2);

      expect(await container.refresh(provider.future), 3);
    });

    test('keeps the saved response on screen when the background fetch fails', () async {
      SharedPreferences.setMockInitialValues({'last_known.k': _saved({'n': 1})});
      final container = ProviderContainer();
      addTearDown(container.dispose);
      final provider = _provider(() async => throw Exception('offline'));
      container.listen(provider, (_, __) {});

      expect(await container.read(provider.future), 1);
      await pumpEventQueue();

      expect(container.read(provider).requireValue, 1);
    });

    test('a fresh response that fails to parse surfaces its error and is not saved', () async {
      SharedPreferences.setMockInitialValues({'last_known.k': _saved({'n': 1})});
      final container = ProviderContainer(retry: (_, __) => null);
      addTearDown(container.dispose);
      final provider = _provider(
        () async => {'status': 'computing'},
        parse: (json) {
          final map = json as Map<String, dynamic>;
          if (map['status'] == 'computing') throw StateError('computing');
          return map['n'] as int;
        },
      );
      container.listen(provider, (_, __) {});

      expect(await container.read(provider.future), 1);
      await pumpEventQueue();

      expect(container.read(provider).error, isA<StateError>());
      expect(await LastKnownStore().read('k'), {'n': 1});
    });

    test('a saved response this build cannot parse is ignored', () async {
      SharedPreferences.setMockInitialValues({'last_known.k': _saved({'unexpected': true})});
      final container = ProviderContainer();
      addTearDown(container.dispose);
      final provider = _provider(() async => {'n': 5});

      expect(await container.read(provider.future), 5);
    });
  });
}

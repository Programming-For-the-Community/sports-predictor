import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/features/events/leaderboard_sort.dart';

void main() {
  num? keyOf(String item) => item.startsWith('-') ? null : int.parse(item.split('.').first);

  test('sorts by key ascending, ties keeping their original order', () {
    expect(sortedByNullableKey(['3', '1.b', '2', '1.a'], keyOf), ['1.b', '1.a', '2', '3']);
  });

  test('items with no key go last, keeping their original order', () {
    expect(sortedByNullableKey(['-x', '2', '-y', '1', '-z'], keyOf), ['1', '2', '-x', '-y', '-z']);
  });

  test('a list with no keys at all keeps its order', () {
    expect(sortedByNullableKey(['-c', '-a', '-b'], keyOf), ['-c', '-a', '-b']);
  });
}

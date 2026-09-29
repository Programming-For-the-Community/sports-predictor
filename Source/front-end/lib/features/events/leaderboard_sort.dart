/// `items` in ascending order of `key`, items with no key after every item
/// that has one. A stable sort: ties, and items with no key, keep their
/// original relative order.
List<T> sortedByNullableKey<T>(List<T> items, num? Function(T item) key) {
  final indexed = [for (var i = 0; i < items.length; i++) (index: i, item: items[i], key: key(items[i]))];
  indexed.sort((a, b) {
    final aKey = a.key;
    final bKey = b.key;
    if (aKey != null && bKey != null) {
      final cmp = aKey.compareTo(bKey);
      if (cmp != 0) return cmp;
    } else if (aKey != null || bKey != null) {
      return aKey != null ? -1 : 1;
    }
    return a.index.compareTo(b.index);
  });
  return [for (final e in indexed) e.item];
}

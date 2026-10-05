import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/widgets/responsive.dart';

void main() {
  test('returns the ideal width when the viewport is wide enough', () {
    expect(cardWidth(340, 800), 340);
  });

  test('shrinks to the available width on a narrower viewport', () {
    expect(cardWidth(340, 327), 327);
  });

  test('returns the ideal width when it exactly matches the available width', () {
    expect(cardWidth(340, 340), 340);
  });

  group('fillCardWidth', () {
    test('stretches one card across a phone row instead of leaving a strip', () {
      // A 411pt phone minus the page's 24pt side padding.
      expect(fillCardWidth(320, 363, spacing: 20), 363);
    });

    test('splits the row evenly once two fit', () {
      expect(fillCardWidth(320, 700, spacing: 20), 340);
    });

    test('shrinks to the available width below the minimum', () {
      expect(fillCardWidth(320, 300, spacing: 20), 300);
    });

    test('three columns fill exactly', () {
      final width = fillCardWidth(320, 1080, spacing: 20);
      expect(width * 3 + 20 * 2, closeTo(1080, 1e-9));
    });
  });
}

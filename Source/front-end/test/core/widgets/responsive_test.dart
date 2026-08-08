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
}

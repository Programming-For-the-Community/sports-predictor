import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/widgets/team_color_dot.dart';

BoxDecoration _decorationOf(WidgetTester tester) {
  final container = tester.widget<Container>(find.byType(Container));
  return container.decoration! as BoxDecoration;
}

void main() {
  group('TeamColorDot', () {
    testWidgets('leaves an already-light color untouched', (tester) async {
      const color = Color(0xFFFFB612); // Steelers gold -- well above the lightness floor
      await tester.pumpWidget(MaterialApp(home: Scaffold(body: TeamColorDot(color: color))));

      expect(_decorationOf(tester).color, color);
    });

    testWidgets('floors a near-black team color to a visibly brighter shade', (tester) async {
      const navy = Color(0xFF002244); // Seahawks navy -- reads as nearly invisible pre-fix
      await tester.pumpWidget(MaterialApp(home: Scaffold(body: TeamColorDot(color: navy))));

      final fill = _decorationOf(tester).color!;
      expect(fill, isNot(navy));
      expect(HSLColor.fromColor(fill).lightness, closeTo(0.32, 0.001));
      // Hue/saturation preserved -- still reads as navy, just brighter, not
      // some unrelated color.
      expect(HSLColor.fromColor(fill).hue, closeTo(HSLColor.fromColor(navy).hue, 0.5));
    });

    testWidgets('renders a near-opaque white ring regardless of fill color', (tester) async {
      await tester.pumpWidget(MaterialApp(home: Scaffold(body: TeamColorDot(color: Colors.black))));

      final border = _decorationOf(tester).border! as Border;
      expect(border.top.color.a, closeTo(0.85, 0.001));
    });
  });
}

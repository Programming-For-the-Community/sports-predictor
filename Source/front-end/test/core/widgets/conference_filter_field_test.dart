import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/widgets/conference_filter_field.dart';

void main() {
  group('ConferenceFilterField', () {
    testWidgets('the clear button empties the displayed text, not just the caller\'s filter state', (tester) async {
      // Regression: an earlier, fully-uncontrolled version only called
      // onChanged('') from the clear button, which reset the caller's
      // own filter state but left the field's own still-typed text
      // visibly unchanged, since nothing had ever told the widget itself
      // to clear.
      var value = 'SEC';
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: StatefulBuilder(
              builder: (context, setState) => ConferenceFilterField(
                value: value,
                onChanged: (v) => setState(() => value = v),
              ),
            ),
          ),
        ),
      );

      expect(find.text('SEC'), findsOneWidget);

      await tester.tap(find.byIcon(Icons.close));
      await tester.pump();

      expect(find.text('SEC'), findsNothing);
      expect(value, '');
    });

    testWidgets('typing calls onChanged with the current text', (tester) async {
      var value = '';
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: StatefulBuilder(
              builder: (context, setState) => ConferenceFilterField(
                value: value,
                onChanged: (v) => setState(() => value = v),
              ),
            ),
          ),
        ),
      );

      await tester.enterText(find.byType(TextField), 'Big Ten');
      await tester.pump();

      expect(value, 'Big Ten');
    });

    testWidgets('the clear button only shows once there is text to clear', (tester) async {
      await tester.pumpWidget(
        MaterialApp(home: Scaffold(body: ConferenceFilterField(value: '', onChanged: (_) {}))),
      );

      expect(find.byIcon(Icons.close), findsNothing);
    });
  });
}

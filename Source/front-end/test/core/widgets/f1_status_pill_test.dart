import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/widgets/f1_status_pill.dart';

Widget _wrap(String? status, {bool dotOnly = false}) =>
    MaterialApp(home: Scaffold(body: F1StatusPill(status: status, dotOnly: dotOnly)));

void main() {
  group('F1StatusPill', () {
    final labelCases = {
      'finished': 'Finished',
      'classified': 'Classified',
      'dnf': 'DNF',
      'dsq': 'DSQ',
      'dns': 'DNS',
    };

    for (final entry in labelCases.entries) {
      testWidgets('shows "${entry.value}" for status "${entry.key}"', (tester) async {
        await tester.pumpWidget(_wrap(entry.key));

        expect(find.text(entry.value), findsOneWidget);
      });
    }

    testWidgets('shows Scheduled for a null status', (tester) async {
      await tester.pumpWidget(_wrap(null));

      expect(find.text('Scheduled'), findsOneWidget);
    });

    testWidgets('uppercases a genuinely unrecognized status rather than guessing a label', (tester) async {
      await tester.pumpWidget(_wrap('retired'));

      expect(find.text('RETIRED'), findsOneWidget);
    });

    testWidgets('dotOnly renders no text label at all', (tester) async {
      await tester.pumpWidget(_wrap('dnf', dotOnly: true));

      expect(find.text('DNF'), findsNothing);
      expect(find.byType(Text), findsNothing);
    });

    testWidgets('dotOnly still surfaces the full label via a tooltip', (tester) async {
      await tester.pumpWidget(_wrap('classified', dotOnly: true));

      expect(find.byTooltip('Classified'), findsOneWidget);
    });
  });
}

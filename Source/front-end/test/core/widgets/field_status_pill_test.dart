import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/widgets/field_status_pill.dart';

Widget _wrap(String? status, {bool dotOnly = false}) =>
    MaterialApp(home: Scaffold(body: FieldStatusPill(status: status, dotOnly: dotOnly)));

void main() {
  group('FieldStatusPill', () {
    final fullWordCases = {
      'scheduled': 'Scheduled',
      'finished': 'Finished',
      'cut': 'Cut',
      'made_cut_did_not_finish': 'Made Cut, DNF',
      'withdrawn': 'Withdrawn',
      'in_progress': 'In Progress',
    };

    for (final entry in fullWordCases.entries) {
      testWidgets('shows the full word "${entry.value}" for status "${entry.key}", not an abbreviation', (tester) async {
        await tester.pumpWidget(_wrap(entry.key));

        expect(find.text(entry.value), findsOneWidget);
      });
    }

    testWidgets('shows -- for a null status', (tester) async {
      await tester.pumpWidget(_wrap(null));

      expect(find.text('--'), findsOneWidget);
    });

    testWidgets('title-cases a genuinely unrecognized status instead of shouting it in uppercase', (tester) async {
      await tester.pumpWidget(_wrap('disqualified'));

      expect(find.text('Disqualified'), findsOneWidget);
      expect(find.text('DISQUALIFIED'), findsNothing);
    });

    testWidgets('dotOnly renders no text label at all', (tester) async {
      await tester.pumpWidget(_wrap('made_cut_did_not_finish', dotOnly: true));

      expect(find.text('Made Cut, DNF'), findsNothing);
      expect(find.byType(Text), findsNothing);
    });

    testWidgets('dotOnly still surfaces the full label via a tooltip', (tester) async {
      await tester.pumpWidget(_wrap('withdrawn', dotOnly: true));

      expect(find.byTooltip('Withdrawn'), findsOneWidget);
    });
  });
}

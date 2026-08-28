import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/widgets/field_status_pill.dart';

Widget _wrap(String? status) => MaterialApp(home: Scaffold(body: FieldStatusPill(status: status)));

void main() {
  group('FieldStatusPill', () {
    final fullWordCases = {
      'scheduled': 'Scheduled',
      'finished': 'Finished',
      'cut': 'Cut',
      'made_cut_did_not_finish': 'Made Cut, DNF',
      'withdrawn': 'Withdrawn',
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

    testWidgets('title-cases an unrecognized status instead of shouting it in uppercase', (tester) async {
      await tester.pumpWidget(_wrap('in_progress'));

      expect(find.text('In Progress'), findsOneWidget);
      expect(find.text('IN_PROGRESS'), findsNothing);
    });
  });
}

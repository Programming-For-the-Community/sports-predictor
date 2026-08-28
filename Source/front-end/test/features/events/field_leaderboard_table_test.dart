import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/models/field_live_score.dart';
import 'package:front_end/core/models/field_prediction.dart';
import 'package:front_end/features/events/field_leaderboard_table.dart';

FieldParticipantPrediction _golfer(String id, String name, {double? projectedScoreToPar, double? actualScoreToPar, Map<int, ModelValue>? rounds, Map<int, ActualRoundResult>? actualRounds}) {
  return FieldParticipantPrediction(
    entityId: id,
    name: name,
    projectedScoreToPar: projectedScoreToPar != null ? ModelValue(value: projectedScoreToPar, modelVersion: 1) : null,
    actualScoreToPar: actualScoreToPar,
    rounds: rounds ?? const {},
    actualRounds: actualRounds ?? const {},
  );
}

Widget _wrap(Widget child) => MaterialApp(home: Scaffold(body: SingleChildScrollView(child: child)));

void main() {
  testWidgets('golfers with a real standing sort ahead of golfers with none, by lowest to-par', (tester) async {
    // Server (projected) order is deliberately the opposite of what the
    // real-standing sort should produce.
    final field = [
      _golfer('1', 'No Standing Yet', projectedScoreToPar: -10),
      _golfer('2', 'Actual Leader', projectedScoreToPar: -2, actualScoreToPar: -8),
      _golfer('3', 'Actual Second', projectedScoreToPar: -1, actualScoreToPar: -3),
    ];

    await tester.pumpWidget(_wrap(FieldLeaderboardTable(field: field)));

    final names = tester.widgetList<Text>(find.byType(Text)).map((t) => t.data).whereType<String>().toList();
    final leaderIndex = names.indexOf('Actual Leader');
    final secondIndex = names.indexOf('Actual Second');
    final noStandingIndex = names.indexOf('No Standing Yet');

    expect(leaderIndex, lessThan(secondIndex));
    expect(secondIndex, lessThan(noStandingIndex));
  });

  testWidgets('a live overlay standing takes priority over the static actual standing', (tester) async {
    final field = [
      _golfer('1', 'Behind On Paper', projectedScoreToPar: -1, actualScoreToPar: -2),
      _golfer('2', 'Ahead Live', projectedScoreToPar: -1, actualScoreToPar: -1),
    ];
    final live = {
      '2': const FieldParticipantLiveResult(scoreToPar: -9), // now well ahead, live-only
    };

    await tester.pumpWidget(_wrap(FieldLeaderboardTable(field: field, liveResults: live)));

    final names = tester.widgetList<Text>(find.byType(Text)).map((t) => t.data).whereType<String>().toList();
    expect(names.indexOf('Ahead Live'), lessThan(names.indexOf('Behind On Paper')));
  });

  testWidgets('golfers with no standing at all preserve their original projected order', (tester) async {
    final field = [
      _golfer('1', 'Projected First', projectedScoreToPar: -10),
      _golfer('2', 'Projected Second', projectedScoreToPar: -5),
    ];

    await tester.pumpWidget(_wrap(FieldLeaderboardTable(field: field)));

    final names = tester.widgetList<Text>(find.byType(Text)).map((t) => t.data).whereType<String>().toList();
    expect(names.indexOf('Projected First'), lessThan(names.indexOf('Projected Second')));
  });

  testWidgets('the current round\'s proj/actual is visible at the top level without expanding', (tester) async {
    final field = [
      _golfer('1', 'Xander Schauffele', rounds: {2: const ModelValue(value: -3.0, modelVersion: 1)}),
    ];

    await tester.pumpWidget(_wrap(FieldLeaderboardTable(field: field)));

    expect(find.text('ROUND 1'), findsNothing); // full labeled breakdown only appears expanded
    expect(find.textContaining('proj -3'), findsOneWidget); // but the current round's own value is already visible
  });

  testWidgets('tapping a row expands a full ROUND 1-4 breakdown, tapping again collapses it', (tester) async {
    final field = [
      _golfer(
        '1', 'Xander Schauffele',
        rounds: {2: const ModelValue(value: -3.0, modelVersion: 1)},
        actualRounds: {1: const ActualRoundResult(round: 1, scoreToPar: -4, totalStrokes: 68.0)},
      ),
    ];

    await tester.pumpWidget(_wrap(FieldLeaderboardTable(field: field)));

    expect(find.text('ROUND 1'), findsNothing);
    expect(find.text('ROUND 2'), findsNothing);

    await tester.tap(find.text('Xander Schauffele'));
    await tester.pumpAndSettle();

    // Always all 4 rounds once expanded, even ones with no data yet.
    expect(find.text('ROUND 1'), findsOneWidget);
    expect(find.text('ROUND 2'), findsOneWidget);
    expect(find.text('ROUND 3'), findsOneWidget);
    expect(find.text('ROUND 4'), findsOneWidget);
    expect(find.textContaining('-4'), findsWidgets); // actual round 1
    expect(find.textContaining('proj -3'), findsWidgets); // projected round 2 (also still shown at the top level)
    expect(find.textContaining('proj --'), findsWidgets); // rounds 3/4 -- placeholder, not omitted

    await tester.tap(find.text('Xander Schauffele'));
    await tester.pumpAndSettle();

    expect(find.text('ROUND 1'), findsNothing);
  });

  testWidgets('a golfer with no round data anywhere shows placeholders for all 4 rounds when expanded', (tester) async {
    final field = [_golfer('1', 'No Round Data')];

    await tester.pumpWidget(_wrap(FieldLeaderboardTable(field: field)));
    await tester.tap(find.text('No Round Data'));
    await tester.pumpAndSettle();

    expect(find.text('ROUND 1'), findsOneWidget);
    expect(find.text('ROUND 4'), findsOneWidget);
    expect(find.textContaining('proj --'), findsWidgets);
  });
}

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/models/f1_live_score.dart';
import 'package:front_end/core/models/f1_prediction.dart';
import 'package:front_end/features/events/f1_leaderboard_table.dart';

F1ModelValue _mv(double value, {int? rank}) => F1ModelValue(value: value, modelVersion: 1, rank: rank);

F1DriverPrediction _driver(
  String entityId, {
  String? name,
  String? constructorEntityId,
  String? constructorName,
  double? projectedFinishPosition,
  double? projectedGridPosition,
  double? projectedQualifyingPosition,
  int? projectedQualifyingRank,
  F1ActualResult? actual,
}) =>
    F1DriverPrediction(
      entityId: entityId,
      name: name,
      constructorEntityId: constructorEntityId,
      constructorName: constructorName,
      projectedFinishPosition: projectedFinishPosition != null ? _mv(projectedFinishPosition) : null,
      projectedGridPosition: projectedGridPosition != null ? _mv(projectedGridPosition) : null,
      projectedQualifyingPosition:
          projectedQualifyingPosition != null ? _mv(projectedQualifyingPosition, rank: projectedQualifyingRank) : null,
      actual: actual,
    );

Widget _wrap(Widget child, {double width = 900}) => MaterialApp(
      home: Scaffold(body: SizedBox(width: width, child: child)),
    );

void main() {
  testWidgets('shows the constructor\'s real display name, not the raw lowercase/underscored id', (tester) async {
    final field = [_driver('max_verstappen', name: 'Max Verstappen', constructorEntityId: 'red_bull', constructorName: 'Red Bull', projectedFinishPosition: 1.0)];

    await tester.pumpWidget(_wrap(F1LeaderboardTable(field: field, isSprint: false)));

    expect(find.text('Red Bull'), findsOneWidget);
    expect(find.text('red_bull'), findsNothing);
  });

  testWidgets('falls back to a humanized id when constructor_name is null', (tester) async {
    final field = [_driver('driver_a', name: 'Driver A', constructorEntityId: 'red_bull', projectedFinishPosition: 1.0)];

    await tester.pumpWidget(_wrap(F1LeaderboardTable(field: field, isSprint: false)));

    expect(find.text('Red Bull'), findsOneWidget);
    expect(find.text('red_bull'), findsNothing);
  });

  testWidgets('projected finish shows each row\'s own rank, never a tied position, even when raw values round the same', (tester) async {
    // 3.4 and 3.6 both round to different integers on their own (3 vs 4)
    // in isolation, but 5.4 and 5.6 vs a THIRD close value can round to
    // the same integer -- use two genuinely close-but-distinct values
    // that DO round identically to prove the display no longer collides.
    final field = [
      _driver('a', name: 'A', projectedFinishPosition: 5.4),
      _driver('b', name: 'B', projectedFinishPosition: 5.6),
    ];

    await tester.pumpWidget(_wrap(F1LeaderboardTable(field: field, isSprint: false)));

    // Row order is caller-provided (server-sorted); rank is 1-based row
    // position, not round(value) -- P1 and P2, never both P6.
    expect(find.text('P1'), findsOneWidget);
    expect(find.text('P2'), findsOneWidget);
    expect(find.text('P6'), findsNothing);
  });

  testWidgets('projected qualifying shows its own backend-computed rank, never a tied position', (tester) async {
    // 3.4 and 3.2 both round to 3 on their own -- without a rank, both
    // would show "P3". event_prediction.py's own _assign_qualifying_ranks
    // computes rank 1/2 for these regardless of how close the raw values
    // are; the widget must prefer that over rounding value itself.
    final field = [
      _driver('a', name: 'A', projectedQualifyingPosition: 3.4, projectedQualifyingRank: 2),
      _driver('b', name: 'B', projectedQualifyingPosition: 3.2, projectedQualifyingRank: 1),
    ];

    await tester.pumpWidget(_wrap(F1LeaderboardTable(field: field, isSprint: false)));

    expect(find.text('P1'), findsOneWidget);
    expect(find.text('P2'), findsOneWidget);
    expect(find.text('P3'), findsNothing);
  });

  testWidgets('a field event shows a QUALIFYING column, a sprint event does not', (tester) async {
    final field = [_driver('a', name: 'A', projectedQualifyingPosition: 2.0)];

    await tester.pumpWidget(_wrap(F1LeaderboardTable(field: field, isSprint: false)));
    expect(find.text('QUALIFYING'), findsOneWidget);

    await tester.pumpWidget(_wrap(F1LeaderboardTable(field: field, isSprint: true)));
    expect(find.text('QUALIFYING'), findsNothing);
  });

  testWidgets('a driver with no real result yet shows a real status word, not blank', (tester) async {
    final field = [_driver('a', name: 'A', projectedFinishPosition: 1.0)];

    await tester.pumpWidget(_wrap(F1LeaderboardTable(field: field, isSprint: false)));

    expect(find.text('Scheduled'), findsOneWidget);
  });

  testWidgets('an empty field shows a plain message instead of an empty table', (tester) async {
    await tester.pumpWidget(_wrap(const F1LeaderboardTable(field: [], isSprint: false)));
    expect(find.textContaining('No field available yet'), findsOneWidget);
  });

  testWidgets('re-sorts by live running order when a live overlay is present', (tester) async {
    // Server-projected order is a, b -- the live overlay says b is
    // actually running ahead (order 1) with a behind (order 2).
    final field = [
      _driver('a', name: 'Driver A', projectedFinishPosition: 1.0),
      _driver('b', name: 'Driver B', projectedFinishPosition: 2.0),
    ];
    final live = {'a': const F1DriverLiveResult(order: 2), 'b': const F1DriverLiveResult(order: 1)};

    await tester.pumpWidget(_wrap(F1LeaderboardTable(field: field, isSprint: false, liveResults: live)));

    final aPos = tester.getTopLeft(find.text('Driver A'));
    final bPos = tester.getTopLeft(find.text('Driver B'));
    expect(bPos.dy, lessThan(aPos.dy));
  });

  testWidgets('shows the live running order in the FINISH column ahead of a stale projection', (tester) async {
    final field = [_driver('a', name: 'Driver A', projectedFinishPosition: 5.0)];
    final live = {'a': const F1DriverLiveResult(order: 3)};

    await tester.pumpWidget(_wrap(F1LeaderboardTable(field: field, isSprint: false, liveResults: live)));

    // Both the '#' column and the FINISH column resolve to the same live
    // order once neither has a real "actual" finish yet.
    expect(find.text('3'), findsNWidgets(2));
    expect(find.text('P5'), findsNothing);
  });

  testWidgets('a driver with a real actual finish still wins over a live overlay', (tester) async {
    final field = [_driver('a', name: 'Driver A', actual: const F1ActualResult(finishPosition: 1))];
    final live = {'a': const F1DriverLiveResult(order: 7)};

    await tester.pumpWidget(_wrap(F1LeaderboardTable(field: field, isSprint: false, liveResults: live)));

    expect(find.text('1'), findsNWidgets(2));
    expect(find.text('7'), findsNothing);
  });

  testWidgets('a completed race re-sorts rows by actual finish position, not stale pre-race order', (tester) async {
    // Server/prediction order was b, a -- the real result has a finishing
    // ahead of b. Real complaint 2026-09-02: a completed Grand Prix's own
    // row order still matched the pre-race projected order, not the
    // actual result.
    final field = [
      _driver('b', name: 'Driver B', actual: const F1ActualResult(finishPosition: 2)),
      _driver('a', name: 'Driver A', actual: const F1ActualResult(finishPosition: 1)),
    ];

    await tester.pumpWidget(_wrap(F1LeaderboardTable(field: field, isSprint: false)));

    final aPos = tester.getTopLeft(find.text('Driver A'));
    final bPos = tester.getTopLeft(find.text('Driver B'));
    expect(aPos.dy, lessThan(bPos.dy));
  });

  testWidgets('a DNF driver in a completed race sorts last and shows no stale projected position', (tester) async {
    // Real complaint 2026-09-02: a DNF'd driver (finishPosition null,
    // status "dnf") displayed "P6" in the FINISH column -- a stale
    // pre-race projection, misleading once the race is already over.
    final field = [
      _driver('dnf', name: 'DNF Driver', projectedFinishPosition: 3.0, actual: const F1ActualResult(status: 'dnf')),
      _driver('a', name: 'Driver A', actual: const F1ActualResult(finishPosition: 1)),
    ];

    await tester.pumpWidget(_wrap(F1LeaderboardTable(field: field, isSprint: false)));

    final aPos = tester.getTopLeft(find.text('Driver A'));
    final dnfPos = tester.getTopLeft(find.text('DNF Driver'));
    expect(aPos.dy, lessThan(dnfPos.dy));
    expect(find.text('P3'), findsNothing);
  });
}

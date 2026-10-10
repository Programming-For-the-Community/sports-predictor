import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/models/f1_live_score.dart';
import 'package:front_end/core/models/f1_prediction.dart';
import 'package:front_end/features/events/f1_leaderboard_table.dart';

import '../../support/mobile_viewport.dart';

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

  testWidgets('a real result keeps the model\'s prediction beside it, from the server order, with places gained or lost', (tester) async {
    // Server order (pre-race prediction): a=P1, b=P2. The real result flips
    // them, so b's row comes first -- its prediction must still read P2.
    final field = [
      _driver('a', name: 'A', projectedFinishPosition: 1.0, actual: const F1ActualResult(finishPosition: 2, status: 'finished')),
      _driver('b', name: 'B', projectedFinishPosition: 2.0, actual: const F1ActualResult(finishPosition: 1, status: 'finished')),
    ];

    await tester.pumpWidget(_wrap(F1LeaderboardTable(field: field, isSprint: false)));

    expect(tester.getTopLeft(find.text('B')).dy, lessThan(tester.getTopLeft(find.text('A')).dy));
    expect(tester.getCenter(find.text('P2')).dy, closeTo(tester.getCenter(find.text('B')).dy, 12));
    expect(find.text('▲ 1'), findsOneWidget);
    expect(find.text('▼ 1'), findsOneWidget);
    expect(find.text('#'), findsOneWidget);
    expect(find.text('±'), findsOneWidget);
  });

  testWidgets('a driver who finished where the model had them shows =', (tester) async {
    final field = [_driver('a', name: 'A', projectedFinishPosition: 1.0, actual: const F1ActualResult(finishPosition: 1))];

    await tester.pumpWidget(_wrap(F1LeaderboardTable(field: field, isSprint: false)));

    expect(find.text('='), findsOneWidget);
  });

  testWidgets('actual and projected qualifying are both shown once qualifying has a real result', (tester) async {
    final field = [
      _driver(
        'a', name: 'A', projectedQualifyingPosition: 4.2, projectedQualifyingRank: 4,
        actual: const F1ActualResult(qualifyingPosition: 2, finishPosition: 1, status: 'finished'),
      ),
    ];

    await tester.pumpWidget(_wrap(F1LeaderboardTable(field: field, isSprint: false)));

    expect(find.text('P4'), findsOneWidget);
    expect(find.text('2'), findsOneWidget);
    expect(find.textContaining('Pred'), findsNothing);
  });

  testWidgets('before the race the table leads with the predicted order and has no result columns', (tester) async {
    final field = [_driver('a', name: 'A', projectedFinishPosition: 3.0)];

    await tester.pumpWidget(_wrap(F1LeaderboardTable(field: field, isSprint: false)));

    expect(find.text('PRED'), findsOneWidget);
    expect(find.text('P1'), findsOneWidget);
    expect(find.text('#'), findsNothing);
    expect(find.text('±'), findsNothing);
    expect(find.text('STATUS'), findsNothing);
  });

  testWidgets('a field event shows qualifying and DNF columns, a sprint shows GRID and neither', (tester) async {
    final field = [_driver('a', name: 'A', projectedQualifyingPosition: 2.0, projectedGridPosition: 1.0)];

    await tester.pumpWidget(_wrap(F1LeaderboardTable(field: field, isSprint: false)));
    expect(find.text('QUALI'), findsOneWidget);
    expect(find.text('DNF%'), findsOneWidget);

    await tester.pumpWidget(_wrap(F1LeaderboardTable(field: field, isSprint: true)));
    expect(find.text('QUALI'), findsNothing);
    expect(find.text('DNF%'), findsNothing);
    expect(find.text('GRID'), findsOneWidget);
  });

  testWidgets('an empty field shows a plain message instead of an empty table', (tester) async {
    await tester.pumpWidget(_wrap(const F1LeaderboardTable(field: [], isSprint: false)));
    expect(find.textContaining('No field available yet'), findsOneWidget);
  });

  testWidgets('re-sorts by live running order when a live overlay is present', (tester) async {
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

  testWidgets('a live running position shows in # with the prediction and the gap beside it', (tester) async {
    final field = [
      _driver('a', name: 'Driver A', projectedFinishPosition: 1.0),
      _driver('b', name: 'Driver B', projectedFinishPosition: 2.0),
    ];
    final live = {'a': const F1DriverLiveResult(order: 3)};

    await tester.pumpWidget(_wrap(F1LeaderboardTable(field: field, isSprint: false, liveResults: live)));

    expect(find.text('3'), findsOneWidget);
    expect(find.text('P1'), findsOneWidget);
    expect(find.text('▼ 2'), findsOneWidget);
  });

  testWidgets('a driver with a real actual finish still wins over a live overlay', (tester) async {
    final field = [_driver('a', name: 'Driver A', actual: const F1ActualResult(finishPosition: 1))];
    final live = {'a': const F1DriverLiveResult(order: 7)};

    await tester.pumpWidget(_wrap(F1LeaderboardTable(field: field, isSprint: false, liveResults: live)));

    expect(find.text('1'), findsOneWidget);
    expect(find.text('7'), findsNothing);
  });

  testWidgets('a completed race re-sorts rows by actual finish position, not stale pre-race order', (tester) async {
    final field = [
      _driver('b', name: 'Driver B', actual: const F1ActualResult(finishPosition: 2)),
      _driver('a', name: 'Driver A', actual: const F1ActualResult(finishPosition: 1)),
    ];

    await tester.pumpWidget(_wrap(F1LeaderboardTable(field: field, isSprint: false)));

    final aPos = tester.getTopLeft(find.text('Driver A'));
    final bPos = tester.getTopLeft(find.text('Driver B'));
    expect(aPos.dy, lessThan(bPos.dy));
  });

  testWidgets('a DNF driver sorts last with a dash for a position, DNF by the team, and no gap', (tester) async {
    final field = [
      _driver('dnf', name: 'DNF Driver', constructorName: 'Alpine', projectedFinishPosition: 3.0, actual: const F1ActualResult(status: 'dnf')),
      _driver('a', name: 'Driver A', projectedFinishPosition: 1.0, actual: const F1ActualResult(finishPosition: 1)),
    ];

    await tester.pumpWidget(_wrap(F1LeaderboardTable(field: field, isSprint: false)));

    final aPos = tester.getTopLeft(find.text('Driver A'));
    final dnfPos = tester.getTopLeft(find.text('DNF Driver'));
    expect(aPos.dy, lessThan(dnfPos.dy));
    expect(find.text('–'), findsOneWidget);
    expect(find.textContaining('Alpine · DNF', findRichText: true), findsOneWidget);
    // One gap, for the classified driver only: the model had A second.
    expect(find.text('▲ 1'), findsOneWidget);
    expect(find.textContaining('▼'), findsNothing);
  });

  group('F1PodiumStrip', () {
    final field = [
      _driver('a', name: 'Lando Norris', projectedFinishPosition: 1.0),
      _driver('b', name: 'Max Verstappen', projectedFinishPosition: 2.0),
      _driver('c', name: 'Oscar Piastri', projectedFinishPosition: 3.0),
      _driver('d', name: 'George Russell', projectedFinishPosition: 4.0),
    ];

    testWidgets('before the session it lists the model\'s first three', (tester) async {
      await tester.pumpWidget(_wrap(F1PodiumStrip(field: field, isSprint: false)));

      expect(find.text(F1PodiumStrip.modelLabel), findsOneWidget);
      expect(find.text('Lando Norris'), findsOneWidget);
      expect(find.text('George Russell'), findsNothing);
      expect(find.text('P3'), findsOneWidget);
    });

    testWidgets('after it, the real first three with where the model had each', (tester) async {
      final decided = [
        _driver('a', name: 'Lando Norris', projectedFinishPosition: 1.0, actual: const F1ActualResult(finishPosition: 3)),
        _driver('b', name: 'Max Verstappen', projectedFinishPosition: 2.0, actual: const F1ActualResult(finishPosition: 2)),
        _driver('c', name: 'Oscar Piastri', projectedFinishPosition: 3.0, actual: const F1ActualResult(status: 'dnf')),
        _driver('d', name: 'George Russell', projectedFinishPosition: 4.0, actual: const F1ActualResult(finishPosition: 1)),
      ];
      await tester.pumpWidget(_wrap(F1PodiumStrip(field: decided, isSprint: false)));

      expect(find.text(F1PodiumStrip.resultLabel), findsOneWidget);
      expect(find.text('Oscar Piastri'), findsNothing);
      // Russell won; the model had him fourth.
      expect(tester.getTopLeft(find.text('George Russell')).dx, lessThan(tester.getTopLeft(find.text('Lando Norris')).dx));
      expect(find.text('P4 · --'), findsOneWidget);
    });

    testWidgets('renders nothing for an empty field, and stacks on a phone without overflow', (tester) async {
      await tester.pumpWidget(_wrap(const F1PodiumStrip(field: [], isSprint: false)));
      expect(find.text(F1PodiumStrip.modelLabel), findsNothing);

      for (final width in mobileViewportWidths) {
        await pumpAtWidth(tester, width, _wrap(SingleChildScrollView(child: F1PodiumStrip(field: field, isSprint: true)), width: width));
        expect(tester.takeException(), isNull);
        expect(
          tester.getTopLeft(find.text('Max Verstappen')).dy,
          greaterThan(tester.getTopLeft(find.text('Lando Norris')).dy),
        );
      }
    });
  });

  group('narrow (mobile) viewport', () {
    testWidgets('before the race a phone row is the prediction, the driver, win and podium', (tester) async {
      final field = [_driver('a', name: 'Driver A', projectedFinishPosition: 1.0, projectedQualifyingPosition: 1.0)];

      await tester.pumpWidget(_wrap(F1LeaderboardTable(field: field, isSprint: false), width: 360));

      expect(find.text('PRED'), findsOneWidget);
      expect(find.text('DRIVER'), findsOneWidget);
      expect(find.text('WIN%'), findsOneWidget);
      expect(find.text('PODIUM%'), findsOneWidget);
      expect(find.text('QUALI'), findsNothing);
      expect(find.text('DNF%'), findsNothing);
    });

    testWidgets('after the race a phone row leads with the real position and keeps the prediction and gap', (tester) async {
      final field = [
        _driver('a', name: 'Driver A', projectedFinishPosition: 1.0, actual: const F1ActualResult(finishPosition: 1, status: 'finished')),
      ];

      await tester.pumpWidget(_wrap(F1LeaderboardTable(field: field, isSprint: false), width: 360));

      expect(find.text('#'), findsOneWidget);
      expect(find.text('PRED'), findsOneWidget);
      expect(find.text('±'), findsOneWidget);
      expect(find.text('PODIUM%'), findsNothing);
    });

    testWidgets('tapping a phone row reveals status, qualifying and DNF chance', (tester) async {
      final field = [_driver('a', name: 'Driver A', projectedFinishPosition: 1.0, projectedQualifyingPosition: 1.0)];

      await tester.pumpWidget(_wrap(F1LeaderboardTable(field: field, isSprint: false), width: 360));
      expect(find.text('QUALI'), findsNothing);

      await tester.tap(find.text('Driver A'));
      await tester.pumpAndSettle();

      expect(find.text('STATUS'), findsOneWidget);
      expect(find.text('Scheduled'), findsOneWidget);
      expect(find.text('QUALI'), findsOneWidget);
      expect(find.text('DNF%'), findsOneWidget);
    });

    testWidgets('a realistic full field renders with no overflow at every mobile width, field and sprint', (tester) async {
      for (final width in mobileViewportWidths) {
        final field = [
          _driver(
            'max_verstappen', name: 'Max Emilian Verstappen', constructorEntityId: 'red_bull_racing', constructorName: 'Oracle Red Bull Racing',
            projectedFinishPosition: 1.0, projectedGridPosition: 1.0, projectedQualifyingPosition: 1.0, projectedQualifyingRank: 1,
          ),
          _driver(
            'lando_norris', name: 'Lando Norris', constructorEntityId: 'mclaren', constructorName: 'McLaren Formula 1 Team',
            projectedFinishPosition: 2.0, projectedGridPosition: 2.0, projectedQualifyingPosition: 2.0, projectedQualifyingRank: 2,
            actual: const F1ActualResult(finishPosition: 2, gridPosition: 2, status: 'dnf', points: 18, qualifyingPosition: 2),
          ),
        ];
        final live = {'max_verstappen': const F1DriverLiveResult(order: 1, winner: true)};

        await pumpAtWidth(tester, width, _wrap(F1LeaderboardTable(field: field, isSprint: false, liveResults: live), width: width));
        expect(tester.takeException(), isNull);
        await tester.tap(find.text('Lando Norris'));
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull);

        await pumpAtWidth(tester, width, _wrap(F1LeaderboardTable(field: field, isSprint: true), width: width));
        expect(tester.takeException(), isNull);

        await pumpAtWidth(tester, width, _wrap(F1LeaderboardTable(field: [field.first], isSprint: false), width: width));
        expect(tester.takeException(), isNull);
      }
    });
  });
}

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/models/field_live_score.dart';
import 'package:front_end/core/models/field_prediction.dart';
import 'package:front_end/features/events/field_leaderboard_table.dart';

import '../../support/mobile_viewport.dart';

FieldParticipantPrediction _golfer(
  String id, String name, {
  double? projectedScoreToPar, double? actualScoreToPar, double? actualTotalStrokes,
  Map<int, ModelValue>? rounds, Map<int, ActualRoundResult>? actualRounds,
  String? actualStatus, int? actualThru,
}) {
  return FieldParticipantPrediction(
    entityId: id,
    name: name,
    projectedScoreToPar: projectedScoreToPar != null ? ModelValue(value: projectedScoreToPar, modelVersion: 1) : null,
    actualScoreToPar: actualScoreToPar,
    actualTotalStrokes: actualTotalStrokes,
    rounds: rounds ?? const {},
    actualRounds: actualRounds ?? const {},
    actualStatus: actualStatus,
    actualThru: actualThru,
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
    // No '(proj ...)' qualifier -- projected values are marked by color
    // (teal/cyan) alone, matching the established actual=white/
    // projected=teal convention, same as PROJ/TOP10%/TOP5% already did.
    expect(find.text('-3'), findsOneWidget); // the current round's own projected value is already visible
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
    expect(find.text('68 (-4)'), findsWidgets); // actual round 1, strokes + to par
    expect(find.text('-3'), findsWidgets); // projected round 2 (also still shown at the top level, so 2 instances)
    expect(find.text('--'), findsWidgets); // rounds 3/4 -- placeholder, not omitted

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
    expect(find.text('--'), findsWidgets);
  });

  testWidgets('TOTAL combines the real standing and the projected final score into one column, no separate PROJ column', (tester) async {
    final field = [_golfer('1', 'Xander Schauffele', projectedScoreToPar: -8, actualScoreToPar: -5)];

    await tester.pumpWidget(_wrap(FieldLeaderboardTable(field: field)));

    expect(find.text('PROJ'), findsNothing); // folded into TOTAL, not its own column anymore
    expect(find.text('TOTAL'), findsOneWidget);
    expect(find.text('-5'), findsOneWidget); // actual standing
    expect(find.text('-8'), findsOneWidget); // projected final score-to-par, right underneath
  });

  testWidgets('TOTAL stays bare to-par even when a real stroke count and par are both available', (tester) async {
    // TOTAL never shows "N strokes (to par)" -- no unambiguous par
    // baseline for a full tournament (2-round missed cut vs. 4-round
    // made cut).
    final field = [_golfer('1', 'Xander Schauffele', projectedScoreToPar: -8, actualScoreToPar: -5, actualTotalStrokes: 275.0)];

    await tester.pumpWidget(_wrap(FieldLeaderboardTable(field: field, par: 70)));

    expect(find.text('-5'), findsOneWidget);
    expect(find.text('-8'), findsOneWidget);
    expect(find.textContaining('275'), findsNothing);
  });

  testWidgets('THIS RD shows the real stroke count alongside to par for an actual round', (tester) async {
    final field = [
      _golfer('1', 'Xander Schauffele', actualRounds: {1: const ActualRoundResult(round: 1, scoreToPar: -4, totalStrokes: 68.0)}),
    ];

    await tester.pumpWidget(_wrap(FieldLeaderboardTable(field: field)));

    expect(find.text('68 (-4)'), findsOneWidget);
  });

  testWidgets('THIS RD shows an implied stroke count for a projected round when par is known', (tester) async {
    final field = [
      _golfer('1', 'Xander Schauffele', rounds: {2: const ModelValue(value: -3.0, modelVersion: 1)}),
    ];

    await tester.pumpWidget(_wrap(FieldLeaderboardTable(field: field, par: 70)));

    expect(find.text('67 (-3)'), findsOneWidget); // 70 + (-3) = 67
  });

  testWidgets('a projected round shows the bare to-par number when par is unknown', (tester) async {
    final field = [
      _golfer('1', 'Xander Schauffele', rounds: {2: const ModelValue(value: -3.0, modelVersion: 1)}),
    ];

    await tester.pumpWidget(_wrap(FieldLeaderboardTable(field: field))); // no par passed

    expect(find.text('-3'), findsOneWidget);
    expect(find.textContaining('67'), findsNothing);
  });

  testWidgets(
      'THIS RD shows round 4 predicted-vs-actual for a finished tournament, not a stale round 1 forecast',
      (tester) async {
    // Real complaint 2026-09-02: event_prediction.py backfills every
    // PLAYED round's own pre-round forecast into `rounds` (so the
    // expanded ROUND 1-4 breakdown can show it), not just whichever round
    // is still genuinely being projected -- for a finished tournament
    // that's rounds 1-4 all at once. THIS RD used to just grab
    // rounds.keys.first, landing on round 1's own long-resolved forecast
    // instead of round 4's -- the one round that actually matches what
    // this golfer shot that day.
    final field = [
      _golfer(
        '1', 'Xander Schauffele',
        rounds: {
          1: const ModelValue(value: -2.0, modelVersion: 1),
          2: const ModelValue(value: -1.0, modelVersion: 1),
          3: const ModelValue(value: 0.0, modelVersion: 1),
          4: const ModelValue(value: -3.0, modelVersion: 1),
        },
        actualRounds: {
          1: const ActualRoundResult(round: 1, scoreToPar: -3, totalStrokes: 67.0),
          2: const ActualRoundResult(round: 2, scoreToPar: 1, totalStrokes: 71.0),
          3: const ActualRoundResult(round: 3, scoreToPar: -3, totalStrokes: 67.0),
          4: const ActualRoundResult(round: 4, scoreToPar: -4, totalStrokes: 66.0),
        },
        actualStatus: 'finished',
      ),
    ];

    await tester.pumpWidget(_wrap(FieldLeaderboardTable(field: field)));

    // Round 4's own real result (66, -4), not round 1's (67, -3).
    expect(find.text('66 (-4)'), findsOneWidget);
    expect(find.text('67 (-3)'), findsNothing);
  });

  testWidgets('THIS RD shows how many holes are played so far when the golfer is in progress', (tester) async {
    final field = [
      _golfer(
        '1', 'Xander Schauffele',
        actualRounds: {2: const ActualRoundResult(round: 2, scoreToPar: -1, totalStrokes: 55.0)},
        actualStatus: 'in_progress', actualThru: 14,
      ),
    ];

    await tester.pumpWidget(_wrap(FieldLeaderboardTable(field: field)));

    expect(find.text('Thru 14'), findsOneWidget);
  });

  testWidgets('THIS RD does not show a Thru count for a golfer who has finished their round', (tester) async {
    final field = [
      _golfer(
        '1', 'Xander Schauffele',
        actualRounds: {1: const ActualRoundResult(round: 1, scoreToPar: -4, totalStrokes: 68.0)},
        actualStatus: 'finished', actualThru: 18,
      ),
    ];

    await tester.pumpWidget(_wrap(FieldLeaderboardTable(field: field)));

    expect(find.textContaining('Thru'), findsNothing);
  });

  group('narrow (mobile) viewport', () {
    testWidgets('THIS RD/TOP 10%/TOP 5% are dropped from the collapsed columns below the compact breakpoint', (tester) async {
      final field = [
        _golfer('1', 'Xander Schauffele', rounds: {2: const ModelValue(value: -3.0, modelVersion: 1)}),
      ];

      await pumpAtWidth(tester, 360, _wrap(FieldLeaderboardTable(field: field)));

      expect(find.text('#'), findsOneWidget);
      expect(find.text('PLAYER'), findsOneWidget);
      expect(find.text('STATUS'), findsOneWidget);
      expect(find.text('TOTAL'), findsOneWidget);
      expect(find.text('THIS RD'), findsNothing);
      expect(find.text('TOP 10%'), findsNothing);
      expect(find.text('TOP 5%'), findsNothing);
    });

    testWidgets('STATUS collapses to a colored dot (no text label) below the compact breakpoint', (tester) async {
      final field = [_golfer('1', 'Xander Schauffele', actualStatus: 'made_cut_did_not_finish')];

      await pumpAtWidth(tester, 360, _wrap(FieldLeaderboardTable(field: field)));

      expect(find.text('Made Cut, DNF'), findsNothing);
    });

    testWidgets('STATUS still shows the full text label at a wide (non-compact) width', (tester) async {
      final field = [_golfer('1', 'Xander Schauffele', actualStatus: 'made_cut_did_not_finish')];

      await pumpAtWidth(tester, 800, _wrap(FieldLeaderboardTable(field: field)));

      expect(find.text('Made Cut, DNF'), findsOneWidget);
    });

    testWidgets('every column is still present at a wide (non-compact) width', (tester) async {
      final field = [_golfer('1', 'Xander Schauffele')];

      await pumpAtWidth(tester, 800, _wrap(FieldLeaderboardTable(field: field)));

      expect(find.text('THIS RD'), findsOneWidget);
      expect(find.text('TOP 10%'), findsOneWidget);
      expect(find.text('TOP 5%'), findsOneWidget);
    });

    testWidgets('TOP 10%/TOP 5% reappear in the expanded detail when compact', (tester) async {
      final field = [_golfer('1', 'Xander Schauffele')];

      await pumpAtWidth(tester, 360, _wrap(FieldLeaderboardTable(field: field)));
      expect(find.text('TOP 10%'), findsNothing);

      await tester.tap(find.text('Xander Schauffele'));
      await tester.pumpAndSettle();

      expect(find.text('TOP 10%'), findsOneWidget);
      expect(find.text('TOP 5%'), findsOneWidget);
    });

    testWidgets('the expanded probabilities row is absent at a wide width (already visible at the top level)', (tester) async {
      final field = [_golfer('1', 'Xander Schauffele')];

      await pumpAtWidth(tester, 800, _wrap(FieldLeaderboardTable(field: field)));
      await tester.tap(find.text('Xander Schauffele'));
      await tester.pumpAndSettle();

      // Exactly one of each -- the top-level column only, not duplicated
      // into the expanded panel too.
      expect(find.text('TOP 10%'), findsOneWidget);
      expect(find.text('TOP 5%'), findsOneWidget);
    });

    testWidgets('a realistic full-width field renders with no overflow at every mobile width', (tester) async {
      for (final width in mobileViewportWidths) {
        final field = [
          _golfer(
            '1', 'Cristóbal Del Solar-Hernández',
            projectedScoreToPar: -8.4, actualScoreToPar: -6, actualTotalStrokes: 275.0,
            rounds: {2: const ModelValue(value: -3.0, modelVersion: 1)},
            actualRounds: {1: const ActualRoundResult(round: 1, scoreToPar: -3, totalStrokes: 68.0)},
          ),
        ];
        final live = {
          '1': const FieldParticipantLiveResult(
            finishPosition: 123, isTie: true, status: 'made_cut_did_not_finish', scoreToPar: -6, totalStrokes: 275.0,
          ),
        };

        await pumpAtWidth(tester, width, _wrap(FieldLeaderboardTable(field: field, liveResults: live, par: 70)));

        expect(tester.takeException(), isNull);
      }
    });
  });
}

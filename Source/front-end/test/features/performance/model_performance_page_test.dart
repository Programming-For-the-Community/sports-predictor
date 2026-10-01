import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/data/model_performance_repository.dart';
import 'package:front_end/core/models/model_performance.dart';
import 'package:front_end/core/widgets/model_performance_accordion.dart';
import 'package:front_end/core/widgets/model_performance_card_view.dart';
import 'package:front_end/features/performance/model_performance_page.dart';

import '../../support/mobile_viewport.dart';
import '../../support/model_performance_fixtures.dart';

ModelPerformance _performance(List<ModelPerformanceRecord> models, {String periodKind = 'week'}) =>
    ModelPerformance(sport: 'nfl', season: 2026, periodKind: periodKind, models: models);

extension on ModelPerformance {
  ModelPerformance copyWithModels(List<ModelPerformanceRecord> models) =>
      ModelPerformance(sport: sport, season: season, periodKind: periodKind, models: models, windowDays: windowDays);
}

Widget _page(Future<ModelPerformance> Function() load) => ProviderScope(
      overrides: [modelPerformanceProvider.overrideWith((ref, sport) => load())],
      child: const MaterialApp(home: Scaffold(body: ModelPerformancePage(sportId: 'nfl'))),
    );

void main() {
  group('orderedPerformanceModels', () {
    test('the headline pick first, then the game numbers, then everything else alphabetically', () {
      final ordered = orderedPerformanceModels([
        pickRecord(modelName: 'zzz-extra'),
        amountRecord(modelName: 'player-prop-sacks'),
        amountRecord(modelName: 'away-score'),
        pickRecord(),
        amountRecord(modelName: 'home-score'),
        amountRecord(modelName: 'player-prop-passing-yards'),
        amountRecord(),
      ]);

      expect(ordered.map((m) => m.modelName), [
        'win-probability',
        'score-margin',
        'home-score',
        'away-score',
        'player-prop-passing-yards',
        'player-prop-sacks',
        'zzz-extra',
      ]);
    });
  });

  testWidgets('shows the season, how far through it, and a card per model', (tester) async {
    await tester.pumpWidget(_page(() async => _performance(fullNflSet())));
    await tester.pumpAndSettle();

    expect(find.text('2026 season · through Wk 3'), findsOneWidget);
    expect(find.byType(ModelPerformanceCardView), findsNWidgets(6));
    expect(find.text('Win Probability'), findsOneWidget);
    expect(find.text('Player Prop Rushing Touchdowns'), findsOneWidget);
  });

  testWidgets('the win probability card comes first', (tester) async {
    await tester.pumpWidget(_page(() async => _performance(fullNflSet().reversed.toList())));
    await tester.pumpAndSettle();

    final firstCardTitle = find.descendant(of: find.byType(ModelPerformanceCardView).first, matching: find.text('Win Probability'));
    expect(firstCardTitle, findsOneWidget);
  });

  testWidgets('a sport graded on a rolling window says so instead of claiming the season', (tester) async {
    await tester.pumpWidget(_page(
      () async => const ModelPerformance(sport: 'ncaambb', season: 2026, periodKind: 'week', models: [], windowDays: 7).copyWithModels([pickRecord()]),
    ));
    await tester.pumpAndSettle();

    expect(find.text('Last 7 days · through Wk 3'), findsOneWidget);
    expect(find.text('LAST 7 DAYS · ACCURACY'), findsOneWidget);
    expect(find.textContaining('THIS SEASON'), findsNothing);
    expect(find.textContaining('2026 season'), findsNothing);
  });

  testWidgets('a sport graded per event says last event on its cards', (tester) async {
    await tester.pumpWidget(_page(() async => _performance([pickRecord()], periodKind: 'event')));
    await tester.pumpAndSettle();

    expect(find.text('LAST EVENT · ACCURACY'), findsOneWidget);
  });

  testWidgets('before any model has results it says so plainly', (tester) async {
    await tester.pumpWidget(_page(() async => _performance([])));
    await tester.pumpAndSettle();

    expect(find.textContaining('No results yet'), findsOneWidget);
    expect(find.byType(ModelPerformanceCardView), findsNothing);
  });

  testWidgets('shows a spinner while loading', (tester) async {
    final completer = Completer<ModelPerformance>();
    await tester.pumpWidget(_page(() => completer.future));
    await tester.pump();

    expect(find.byType(CircularProgressIndicator), findsOneWidget);

    completer.complete(_performance([]));
    await tester.pumpAndSettle();
  });

  testWidgets('shows the error when the scorecard cannot be loaded', (tester) async {
    await tester.pumpWidget(_page(() async => throw Exception('boom')));
    await tester.pumpAndSettle();

    expect(find.textContaining("Couldn't load performance"), findsOneWidget);
  });

  Future<void> pumpDesktop(WidgetTester tester, double width) async {
    tester.view.physicalSize = Size(width, 2400);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(_page(() async => _performance(fullNflSet())));
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
  }

  Rect cardRect(WidgetTester tester, int i) => tester.getRect(find.byType(ModelPerformanceCardView).at(i));

  testWidgets('on a 1366px laptop the cards sit two to a row, filling the width', (tester) async {
    await pumpDesktop(tester, 1366);

    final first = cardRect(tester, 0);
    final second = cardRect(tester, 1);
    expect(second.top, first.top);
    expect(first.width, second.width);
    expect(second.right, closeTo(1366 - 24, 0.5), reason: 'the row reaches the page padding -- no empty strip');
  });

  testWidgets('on a 1920px screen the cards sit three to a row', (tester) async {
    await pumpDesktop(tester, 1920);

    final tops = [for (var i = 0; i < 3; i++) cardRect(tester, i).top];
    expect(tops.toSet(), hasLength(1));
    expect(cardRect(tester, 3).top, greaterThan(cardRect(tester, 0).bottom));
  });

  testWidgets('cards in the same row are the same height, with recent weeks lined up along the bottom', (tester) async {
    await pumpDesktop(tester, 1920);

    final row = [for (var i = 0; i < 3; i++) cardRect(tester, i)];
    expect(row.map((r) => r.height).toSet(), hasLength(1));
    final recentBottoms = [
      for (final label in find.text('RECENT WEEKS').evaluate().take(3)) tester.getRect(find.byWidget(label.widget)).top,
    ];
    expect(recentBottoms.toSet(), hasLength(1));
  });

  testWidgets('on a narrower desktop window the cards are one to a row', (tester) async {
    await pumpDesktop(tester, 1100);

    expect(cardRect(tester, 1).top, greaterThan(cardRect(tester, 0).bottom));
  });

  for (final width in [320.0, 360.0, 375.0, 390.0]) {
    testWidgets('on a ${width.toInt()}px phone each model is a row that opens with no overflow or clipped text', (tester) async {
      await pumpAtWidth(tester, width, _page(() async => _performance(fullNflSet())));

      expect(find.byType(ModelPerformanceAccordion), findsOneWidget);
      expect(find.byType(ModelPerformanceCardView), findsNothing);
      for (final name in ['Win Probability', 'Score Margin', 'Player Prop Passing Yards', 'Player Prop Rushing Touchdowns']) {
        await tester.ensureVisible(find.text(name));
        await tester.tap(find.text(name));
        await tester.pumpAndSettle();
        expect(find.byType(ModelPerformanceDetails), findsOneWidget, reason: '$name opens on its own');
        expect(truncatedText(tester), isEmpty, reason: '$name, opened, clips no text at ${width}px');
      }
      expect(tester.takeException(), isNull);
    });
  }

  testWidgets('with 600px or more for the cards (a tablet) the models are full cards in the grid', (tester) async {
    await pumpAtWidth(tester, 648, _page(() async => _performance(fullNflSet())));

    expect(find.byType(ModelPerformanceAccordion), findsNothing);
    expect(find.byType(ModelPerformanceCardView), findsNWidgets(6));
  });

  testWidgets('a rolling-window sport labels the season column with its window', (tester) async {
    await pumpAtWidth(
      tester,
      390,
      _page(() async => ModelPerformance(sport: 'ncaambb', season: 2026, periodKind: 'week', windowDays: 7, models: [pickRecord()])),
    );

    expect(find.text('LAST 7 DAYS'), findsOneWidget);
  });

  testWidgets('pull-to-refresh refetches the performance', (tester) async {
    var calls = 0;
    await tester.pumpWidget(_page(() async {
      calls++;
      return _performance([pickRecord()]);
    }));
    await tester.pumpAndSettle();

    await tester.widget<RefreshIndicator>(find.byType(RefreshIndicator)).onRefresh();
    await tester.pumpAndSettle();

    expect(calls, 2);
  });
}

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/data/model_performance_repository.dart';
import 'package:front_end/core/models/model_performance.dart';
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

  testWidgets('on a desktop screen each card is one wide row', (tester) async {
    tester.view.physicalSize = const Size(1100, 1800);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    await tester.pumpWidget(_page(() async => _performance(fullNflSet())));
    await tester.pumpAndSettle();

    expect(tester.takeException(), isNull);
    final first = tester.getRect(find.byType(ModelPerformanceCardView).at(0));
    final second = tester.getTopLeft(find.byType(ModelPerformanceCardView).at(1));
    expect(first.width, 880);
    expect(second.dy, greaterThan(first.bottom));
  });

  testWidgets('on a very wide screen the wide cards sit two to a row without crashing', (tester) async {
    tester.view.physicalSize = const Size(1900, 1800);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    await tester.pumpWidget(_page(() async => _performance(fullNflSet())));
    await tester.pumpAndSettle();

    expect(tester.takeException(), isNull);
    final first = tester.getTopLeft(find.byType(ModelPerformanceCardView).at(0));
    final second = tester.getTopLeft(find.byType(ModelPerformanceCardView).at(1));
    expect(second.dy, first.dy);
    expect(second.dx, greaterThan(first.dx));
  });

  for (final width in [320.0, 360.0, 375.0, 390.0]) {
    testWidgets('on a ${width.toInt()}px phone the cards are one column with no overflow or clipped text', (tester) async {
      await pumpAtWidth(tester, width, _page(() async => _performance(fullNflSet())));

      expect(tester.takeException(), isNull);
      final xs = {
        for (var i = 0; i < 6; i++) tester.getTopLeft(find.byType(ModelPerformanceCardView).at(i)).dx,
      };
      expect(xs, hasLength(1), reason: 'every card starts at the same left edge -- a single column');
    });
  }
}

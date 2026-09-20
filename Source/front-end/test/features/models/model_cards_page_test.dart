import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/data/models_repository.dart';
import 'package:front_end/core/models/model_card.dart';
import 'package:front_end/core/widgets/model_card_view.dart';
import 'package:front_end/features/models/model_cards_page.dart';

ModelCard _card(String name) => ModelCard(
      modelName: name,
      algorithm: 'xgboost',
      version: 12,
      trainedAt: '2026-08-01T00:00:00Z',
      topFeatures: const [],
      accuracy: 0.68,
      logLoss: 0.59,
      naiveBaselineAccuracy: 0.58,
      rmse: null,
      mae: null,
      naiveBaselineMae: null,
      candidates: null,
      candidatesRankedBy: null,
    );

Widget _page({required Future<List<ModelCard>> Function() models}) {
  return ProviderScope(
    overrides: [modelsListProvider.overrideWith((ref, sport) => models())],
    child: const MaterialApp(home: Scaffold(body: ModelCardsPage(sportId: 'nfl'))),
  );
}

Future<void> _pumpAtWidth(WidgetTester tester, double width, Widget widget) async {
  tester.view.physicalSize = Size(width, 1200);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(widget);
  await tester.pumpAndSettle();
}

void main() {
  group('ModelCardsPage', () {
    testWidgets('renders a card per model', (tester) async {
      await tester.pumpWidget(_page(models: () async => [_card('win-probability'), _card('score-margin')]));
      await tester.pumpAndSettle();

      expect(find.text('Win Probability'), findsOneWidget);
      expect(find.text('Score Margin'), findsOneWidget);
    });

    testWidgets('shows a message when no models have been promoted', (tester) async {
      await tester.pumpWidget(_page(models: () async => []));
      await tester.pumpAndSettle();

      expect(find.text('No models have been promoted yet.'), findsOneWidget);
    });

    testWidgets('shows a loading spinner while fetching', (tester) async {
      await tester.pumpWidget(_page(models: () => Completer<List<ModelCard>>().future));

      expect(find.byType(CircularProgressIndicator), findsOneWidget);
    });

    testWidgets('shows an error message when the fetch fails', (tester) async {
      await tester.pumpWidget(_page(models: () async => throw Exception('network down')));
      await tester.pumpAndSettle();

      expect(find.textContaining('Couldn\'t load models'), findsOneWidget);
    });

    testWidgets('lays out multiple cards side by side on a wide viewport', (tester) async {
      await _pumpAtWidth(
        tester, 1200,
        _page(models: () async => [_card('win-probability'), _card('score-margin')]),
      );

      final firstTop = tester.getTopLeft(find.byType(ModelCardView).at(0)).dy;
      final secondTop = tester.getTopLeft(find.byType(ModelCardView).at(1)).dy;
      expect(firstTop, secondTop);
    });

    testWidgets('stacks cards vertically on a narrow viewport', (tester) async {
      await _pumpAtWidth(
        tester, 375,
        _page(models: () async => [_card('win-probability'), _card('score-margin')]),
      );

      final firstTop = tester.getTopLeft(find.byType(ModelCardView).at(0)).dy;
      final secondTop = tester.getTopLeft(find.byType(ModelCardView).at(1)).dy;
      expect(secondTop, greaterThan(firstTop));
    });
  });
}

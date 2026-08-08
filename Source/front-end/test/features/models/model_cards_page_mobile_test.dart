import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/data/models_repository.dart';
import 'package:front_end/core/models/model_card.dart';
import 'package:front_end/features/models/model_cards_page.dart';

import '../../support/mobile_viewport.dart';

ModelCard _classifierCard(String name) => ModelCard(
      modelName: name,
      algorithm: 'xgboost',
      version: 12,
      trainedAt: '2026-08-01T00:00:00Z',
      topFeatures: const [
        ModelFeatureImportance(feature: 'home_elo_rating', importance: 0.42),
        ModelFeatureImportance(feature: 'away_travel_km', importance: 0.31),
      ],
      accuracy: 0.68,
      logLoss: 0.59,
      naiveBaselineAccuracy: 0.58,
      rmse: null,
      mae: null,
      naiveBaselineMae: null,
      candidates: const [
        ModelCandidate(algorithm: 'xgboost', score: 0.68, rankScore: 0.59),
        ModelCandidate(algorithm: 'logistic_regression', score: 0.64, rankScore: 0.63),
      ],
      candidatesRankedBy: 'log_loss',
    );

void main() {
  for (final width in mobileViewportWidths) {
    testWidgets('renders with no overflow at ${width}px wide', (tester) async {
      await pumpAtWidth(
        tester,
        width,
        ProviderScope(
          overrides: [
            modelsListProvider.overrideWith(
              (ref, sport) async => [
                _classifierCard('win-probability'),
                _classifierCard('player-prop-receiving-touchdowns'),
              ],
            ),
          ],
          child: const MaterialApp(home: Scaffold(body: ModelCardsPage(sportId: 'nfl'))),
        ),
      );

      expect(tester.takeException(), isNull);
    });
  }
}

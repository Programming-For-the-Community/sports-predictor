import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/models/model_card.dart';

void main() {
  test('parses a classifier model card (accuracy/log_loss)', () {
    final card = ModelCard.fromJson({
      'model_name': 'win-probability',
      'algorithm': 'xgboost',
      'version': 6,
      'trained_at': '2026-01-01T00:00:00Z',
      'accuracy': 0.63,
      'log_loss': 0.65,
      'top_features': [
        {'feature': 'elo_diff', 'importance': 0.22},
      ],
    });

    expect(card.isClassifier, isTrue);
    expect(card.accuracy, 0.63);
    expect(card.rmse, isNull);
    expect(card.topFeatures.single.feature, 'elo_diff');
  });

  test('parses a regressor model card (rmse/mae)', () {
    final card = ModelCard.fromJson({
      'model_name': 'score-margin',
      'algorithm': 'xgboost',
      'version': 2,
      'trained_at': '2026-01-01T00:00:00Z',
      'rmse': 9.8,
      'mae': 7.4,
      'top_features': [],
    });

    expect(card.isClassifier, isFalse);
    expect(card.rmse, 9.8);
    expect(card.topFeatures, isEmpty);
  });
}

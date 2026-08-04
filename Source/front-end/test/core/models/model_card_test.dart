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
    expect(card.naiveBaselineAccuracy, isNull);
    expect(card.topFeatures.single.feature, 'elo_diff');
    // This card predates the backtesting harness -- null, not an empty list.
    expect(card.candidates, isNull);
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
    expect(card.naiveBaselineMae, isNull);
    expect(card.topFeatures, isEmpty);
  });

  test('parses naive baseline fields when present', () {
    final classifier = ModelCard.fromJson({
      'model_name': 'win-probability',
      'algorithm': 'xgboost',
      'version': 6,
      'trained_at': '2026-01-01T00:00:00Z',
      'accuracy': 0.63,
      'log_loss': 0.65,
      'naive_baseline_accuracy': 0.57,
      'top_features': [],
    });
    expect(classifier.naiveBaselineAccuracy, 0.57);

    final regressor = ModelCard.fromJson({
      'model_name': 'score-margin',
      'algorithm': 'xgboost',
      'version': 2,
      'trained_at': '2026-01-01T00:00:00Z',
      'rmse': 9.8,
      'mae': 7.4,
      'naive_baseline_mae': 9.6,
      'top_features': [],
    });
    expect(regressor.naiveBaselineMae, 9.6);
  });

  test('parses the candidate tournament summary when present, already ranked best-first', () {
    final card = ModelCard.fromJson({
      'model_name': 'win-probability',
      'algorithm': 'xgboost',
      'version': 6,
      'trained_at': '2026-01-01T00:00:00Z',
      'accuracy': 0.63,
      'log_loss': 0.65,
      'top_features': [],
      'candidates': [
        {'algorithm': 'xgboost', 'score': 0.63},
        {'algorithm': 'logistic_regression', 'score': 0.58},
      ],
    });

    expect(card.candidates, hasLength(2));
    expect(card.candidates![0].algorithm, 'xgboost');
    // "score" is deliberately accuracy/mae, not the raw log_loss/rmse
    // used to rank -- see backtest.py's own reasoning.
    expect(card.candidates![0].score, 0.63);
    expect(card.candidates![1].algorithm, 'logistic_regression');
  });
}

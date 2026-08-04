import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/models/model_card.dart';
import 'package:front_end/core/widgets/model_card_view.dart';

Future<void> _pump(WidgetTester tester, ModelCard model) async {
  await tester.pumpWidget(MaterialApp(home: Scaffold(body: ModelCardView(model: model))));
}

ModelCard _classifierCard({List<ModelCandidate>? candidates}) => ModelCard(
      modelName: 'win-probability',
      algorithm: 'xgboost',
      version: 6,
      trainedAt: '2026-01-01T00:00:00Z',
      topFeatures: const [],
      accuracy: 0.63,
      logLoss: 0.65,
      naiveBaselineAccuracy: 0.57,
      rmse: null,
      mae: null,
      naiveBaselineMae: null,
      candidates: candidates,
    );

ModelCard _regressorCard({List<ModelCandidate>? candidates}) => ModelCard(
      modelName: 'score-margin',
      algorithm: 'xgboost',
      version: 2,
      trainedAt: '2026-01-01T00:00:00Z',
      topFeatures: const [],
      accuracy: null,
      logLoss: null,
      naiveBaselineAccuracy: null,
      rmse: 9.8,
      mae: 7.4,
      naiveBaselineMae: 9.6,
      candidates: candidates,
    );

void main() {
  testWidgets('hides the comparison section when no candidates were recorded', (tester) async {
    await _pump(tester, _classifierCard());

    expect(find.text('COMPARED AGAINST'), findsNothing);
  });

  testWidgets('hides the comparison section for a single-candidate run', (tester) async {
    await _pump(tester, _classifierCard(candidates: const [ModelCandidate(algorithm: 'xgboost', score: 0.63)]));

    expect(find.text('COMPARED AGAINST'), findsNothing);
  });

  testWidgets('shows every candidate as an accuracy percentage for a classifier', (tester) async {
    await _pump(
      tester,
      _classifierCard(candidates: const [
        ModelCandidate(algorithm: 'xgboost', score: 0.63),
        ModelCandidate(algorithm: 'logistic_regression', score: 0.58),
      ]),
    );

    expect(find.text('COMPARED AGAINST'), findsOneWidget);
    expect(find.text('XGBoost'), findsOneWidget);
    expect(find.text('Logistic Regression'), findsOneWidget);
    // 63.0% appears twice by coincidence here -- once as the card's own
    // primary ACCURACY stat, once as xgboost's own candidate row, since
    // xgboost is both this card's algorithm and a candidate in its own
    // tournament.
    expect(find.text('63.0%'), findsNWidgets(2));
    expect(find.text('58.0%'), findsOneWidget);
    // Never renders the raw log_loss anywhere in the comparison.
    expect(find.text('0.65'), findsNothing);
  });

  testWidgets('shows every candidate as a +/- error range for a regressor', (tester) async {
    await _pump(
      tester,
      _regressorCard(candidates: const [
        ModelCandidate(algorithm: 'xgboost', score: 7.4),
        ModelCandidate(algorithm: 'elastic_net', score: 9.1),
      ]),
    );

    expect(find.text('±7.4'), findsOneWidget);
    expect(find.text('±9.1'), findsOneWidget);
    expect(find.text('ElasticNet'), findsOneWidget);
  });

  testWidgets('marks the currently promoted algorithm', (tester) async {
    await _pump(
      tester,
      _classifierCard(candidates: const [
        ModelCandidate(algorithm: 'xgboost', score: 0.63),
        ModelCandidate(algorithm: 'random_forest_classifier', score: 0.60),
      ]),
    );

    expect(find.text('PROMOTED'), findsOneWidget);
    expect(find.text('Random Forest'), findsOneWidget);
  });
}

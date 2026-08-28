import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/models/model_card.dart';
import 'package:front_end/core/widgets/model_card_view.dart';

Future<void> _pump(WidgetTester tester, ModelCard model) async {
  await tester.pumpWidget(MaterialApp(home: Scaffold(body: ModelCardView(model: model))));
}

ModelCard _classifierCard({List<ModelCandidate>? candidates, String? candidatesRankedBy}) => ModelCard(
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
      candidatesRankedBy: candidatesRankedBy,
    );

ModelCard _regressorCard({List<ModelCandidate>? candidates, String? candidatesRankedBy, String modelName = 'score-margin'}) => ModelCard(
      modelName: modelName,
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
      candidatesRankedBy: candidatesRankedBy,
    );

void main() {
  testWidgets('VS BASELINE uses the same relative-percentage format for a classifier and a regressor', (tester) async {
    // accuracy=0.63 vs naiveBaselineAccuracy=0.57 ->
    // (0.63-0.57)/0.57*100 ~= 10.5% -> "11%".
    await _pump(tester, _classifierCard());
    expect(find.text('+11% BETTER'), findsOneWidget);
  });

  testWidgets('VS BASELINE for a regressor is also a relative percentage', (tester) async {
    // mae=7.4 vs naiveBaselineMae=9.6 -> (9.6-7.4)/9.6*100 ~= 22.9% -> "23%".
    await _pump(tester, _regressorCard());
    expect(find.text('+23% BETTER'), findsOneWidget);
  });

  testWidgets('hides the comparison section when no candidates were recorded', (tester) async {
    await _pump(tester, _classifierCard());

    expect(find.text('COMPARED AGAINST'), findsNothing);
  });

  testWidgets('hides the comparison section for a single-candidate run', (tester) async {
    await _pump(
      tester,
      _classifierCard(candidates: const [ModelCandidate(algorithm: 'xgboost', score: 0.63, rankScore: 0.65)]),
    );

    expect(find.text('COMPARED AGAINST'), findsNothing);
  });

  testWidgets('shows every candidate as an accuracy percentage for a classifier', (tester) async {
    await _pump(
      tester,
      _classifierCard(
        candidatesRankedBy: 'log_loss',
        candidates: const [
          ModelCandidate(algorithm: 'xgboost', score: 0.63, rankScore: 0.65),
          ModelCandidate(algorithm: 'logistic_regression', score: 0.58, rankScore: 0.71),
        ],
      ),
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

  testWidgets('shows every candidate as a +/- error range with its unit for a regressor', (tester) async {
    await _pump(
      tester,
      _regressorCard(
        modelName: 'score-margin',
        candidatesRankedBy: 'rmse',
        candidates: const [
          ModelCandidate(algorithm: 'xgboost', score: 7.4, rankScore: 9.8),
          ModelCandidate(algorithm: 'elastic_net', score: 9.1, rankScore: 10.4),
        ],
      ),
    );

    expect(find.text('±7.4 PTS'), findsOneWidget);
    expect(find.text('±9.1 PTS'), findsOneWidget);
    expect(find.text('ElasticNet'), findsOneWidget);
  });

  testWidgets('a not-yet-evaluated candidate (null score) shows a real label instead of crashing', (tester) async {
    // A model card captured mid-run can permanently carry a
    // "not_evaluated" placeholder candidate (library/ml/backtest.py's
    // _full_candidate_summary) if that training run never finished.
    await _pump(
      tester,
      _regressorCard(
        candidatesRankedBy: 'rmse',
        candidates: const [
          ModelCandidate(algorithm: 'xgboost', score: 7.4, rankScore: 9.8),
          ModelCandidate(algorithm: 'mlp_regressor', score: null, rankScore: null, status: 'not_evaluated'),
        ],
      ),
    );

    expect(find.text('±7.4 PTS'), findsOneWidget);
    expect(find.text('Not yet evaluated'), findsOneWidget);
  });

  testWidgets('uses the unit matching the target stat for a player-prop model', (tester) async {
    await _pump(
      tester,
      _regressorCard(
        modelName: 'player-prop-passing-yards',
        candidatesRankedBy: 'rmse',
        candidates: const [
          ModelCandidate(algorithm: 'xgboost', score: 24.6, rankScore: 31.2),
          ModelCandidate(algorithm: 'mlp_regressor', score: 27.1, rankScore: 33.5),
        ],
      ),
    );

    expect(find.text('±24.6 YDS'), findsOneWidget);
    expect(find.text('±27.1 YDS'), findsOneWidget);
  });

  testWidgets('marks the currently promoted algorithm', (tester) async {
    await _pump(
      tester,
      _classifierCard(
        candidatesRankedBy: 'log_loss',
        candidates: const [
          ModelCandidate(algorithm: 'xgboost', score: 0.63, rankScore: 0.65),
          ModelCandidate(algorithm: 'random_forest_classifier', score: 0.66, rankScore: 0.68),
        ],
      ),
    );

    expect(find.text('PROMOTED'), findsOneWidget);
    expect(find.text('Random Forest'), findsOneWidget);
  });
}

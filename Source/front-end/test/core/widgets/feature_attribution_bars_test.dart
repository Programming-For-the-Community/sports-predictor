import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/models/model_card.dart';
import 'package:front_end/core/widgets/feature_attribution_bars.dart';

void main() {
  group('FeatureAttributionBars', () {
    testWidgets('shows a message when there are no features', (tester) async {
      await tester.pumpWidget(const MaterialApp(home: Scaffold(body: FeatureAttributionBars(features: []))));

      expect(find.text('No feature importance recorded.'), findsOneWidget);
    });

    testWidgets('renders every feature name and its importance value', (tester) async {
      await tester.pumpWidget(const MaterialApp(
        home: Scaffold(
          body: FeatureAttributionBars(features: [
            ModelFeatureImportance(feature: 'home_elo_rating', importance: 0.42),
            ModelFeatureImportance(feature: 'away_travel_km', importance: 0.18),
          ]),
        ),
      ));

      expect(find.text('home_elo_rating'), findsOneWidget);
      expect(find.text('0.42'), findsOneWidget);
      expect(find.text('away_travel_km'), findsOneWidget);
      expect(find.text('0.18'), findsOneWidget);
    });

    testWidgets('does not crash when every feature has zero importance', (tester) async {
      await tester.pumpWidget(const MaterialApp(
        home: Scaffold(
          body: FeatureAttributionBars(features: [
            ModelFeatureImportance(feature: 'unused_feature', importance: 0.0),
          ]),
        ),
      ));

      expect(tester.takeException(), isNull);
      expect(find.text('unused_feature'), findsOneWidget);
    });
  });
}

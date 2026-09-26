import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/models/model_performance.dart';
import 'package:front_end/core/widgets/model_performance_card_view.dart';

import '../../support/mobile_viewport.dart';

Map<String, dynamic> _entity(String id, double value, int n, {String? name, String? abbreviation, String? color}) => {
      'entity_id': id, 'value': value, 'n': n, 'name': name, 'abbreviation': abbreviation, 'color': color,
    };

ModelPerformanceRecord _record({
  required String modelName,
  required String kind,
  required String bandKind,
  required Map<String, dynamic> best,
  Map<String, dynamic>? bestRelative,
}) =>
    ModelPerformanceRecord.fromJson({
      'model_name': modelName, 'version': 1, 'kind': kind, 'band_kind': bandKind,
      'season': {'value': kind == 'amount' ? 12.6 : 0.714, 'n': 266},
      'last_period': {'label': 'Wk 3', 'value': kind == 'amount' ? 11.9 : 0.742, 'n': 93},
      'periods': <dynamic>[], 'bands': <dynamic>[],
      'best': best,
      if (bestRelative != null) 'best_relative': bestRelative,
    });

ModelPerformanceRecord _winProbability() => _record(
      modelName: 'win-probability', kind: 'pick', bandKind: 'confidence',
      best: {
        'entity_type': 'team',
        'entities': [
          _entity('61', 1.0, 4, name: 'Georgia Bulldogs', abbreviation: 'UGA', color: 'ba0c2f'),
          _entity('194', 0.75, 4, name: 'Ohio State Buckeyes', abbreviation: 'OSU'),
        ],
      },
    );

ModelPerformanceRecord _margin() => _record(
      modelName: 'score-margin', kind: 'amount', bandKind: 'predicted_amount',
      best: {'entity_type': 'team', 'entities': [_entity('333', 2.3, 3, name: 'Alabama Crimson Tide', abbreviation: 'ALA')]},
    );

ModelPerformanceRecord _rushingYards() => _record(
      modelName: 'player-prop-rushing-yards', kind: 'amount', bandKind: 'predicted_amount',
      best: {'entity_type': 'player', 'entities': [_entity('9', 1.9, 3, name: 'Demond Williams Jr.')]},
      bestRelative: {'entity_type': 'player', 'entities': [_entity('4', 0.06, 3, name: 'Quinshon Judkins')]},
    );

Widget _card(ModelPerformanceRecord record, {double width = 460}) => MaterialApp(
      home: Scaffold(
        body: SingleChildScrollView(
          child: SizedBox(width: width, child: ModelPerformanceCardView(record: record, isWeekly: true, sport: 'ncaafb')),
        ),
      ),
    );

void main() {
  test('parses a ranking from the scorecard', () {
    final best = _winProbability().best!;

    expect(best.entityType, 'team');
    expect(best.entities.map((e) => (e.abbreviation, e.value, e.n)), [('UGA', 1.0, 4), ('OSU', 0.75, 4)]);
  });

  test('an older scorecard without rankings parses with none', () {
    final record = ModelPerformanceRecord.fromJson({
      'model_name': 'win-probability', 'kind': 'pick', 'band_kind': 'confidence',
      'season': {'value': null, 'n': 0},
    });

    expect(record.best, isNull);
    expect(record.bestRelative, isNull);
  });

  testWidgets('win probability shows the leading team with how many it picked right, then the rest', (tester) async {
    await tester.pumpWidget(_card(_winProbability()));

    expect(find.text('MOST ACCURATE ON'), findsOneWidget);
    expect(find.text('Georgia Bulldogs'), findsOneWidget);
    expect(find.text('UGA'), findsOneWidget);
    expect(find.text('100%'), findsOneWidget);
    expect(find.text('4 / 4'), findsOneWidget);
    expect(find.text('2'), findsOneWidget);
    expect(find.textContaining('Ohio State Buckeyes'), findsOneWidget);
    expect(find.textContaining('75%'), findsOneWidget);
  });

  testWidgets('an amount model shows the smallest average miss in its unit', (tester) async {
    await tester.pumpWidget(_card(_margin()));

    expect(find.text('Alabama Crimson Tide'), findsOneWidget);
    expect(find.text('2.3 pts'), findsOneWidget);
    expect(find.text('avg miss'), findsOneWidget);
  });

  testWidgets('a player prop starts on share of actual and toggles to the raw miss', (tester) async {
    await tester.pumpWidget(_card(_rushingYards()));

    expect(find.text('Quinshon Judkins'), findsOneWidget);
    expect(find.text('6%'), findsOneWidget);
    expect(find.text('of actual'), findsOneWidget);

    await tester.tap(find.text('RAW YDS'));
    await tester.pump();

    expect(find.text('Demond Williams Jr.'), findsOneWidget);
    expect(find.text('1.9 yds'), findsOneWidget);
    expect(find.text('Quinshon Judkins'), findsNothing);
  });

  testWidgets('a team or game model has no toggle', (tester) async {
    await tester.pumpWidget(_card(_margin()));

    expect(find.text('% OF ACTUAL'), findsNothing);
  });

  testWidgets('with nothing ranked yet it says so', (tester) async {
    await tester.pumpWidget(_card(_record(
      modelName: 'score-margin', kind: 'amount', bandKind: 'predicted_amount',
      best: {'entity_type': 'team', 'entities': <dynamic>[]},
    )));

    expect(find.text('Not enough graded games yet'), findsOneWidget);
  });

  testWidgets('a card from an older scorecard has no section', (tester) async {
    final record = ModelPerformanceRecord.fromJson({
      'model_name': 'win-probability', 'kind': 'pick', 'band_kind': 'confidence',
      'season': {'value': 0.7, 'n': 10}, 'bands': <dynamic>[],
    });

    await tester.pumpWidget(_card(record));

    expect(find.text('MOST ACCURATE ON'), findsNothing);
  });

  for (final width in mobileViewportWidths) {
    testWidgets('renders with no overflow or clipped text at ${width}px wide', (tester) async {
      for (final record in [_winProbability(), _margin(), _rushingYards()]) {
        await pumpAtWidth(tester, width, _card(record, width: width - 32));
        expect(tester.takeException(), isNull);
      }
    });
  }
}

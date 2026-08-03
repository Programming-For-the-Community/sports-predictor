import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/models/prediction.dart';

Map<String, dynamic> _basePrediction() => {
      'predictions': {
        'win_probability': {'home_win_probability': 0.62, 'model_version': 6},
        'margin': {'value': 3.2, 'model_version': 2},
        'home_score': {'value': 24.1, 'model_version': 1},
        'away_score': {'value': 20.9, 'model_version': 1},
      },
    };

void main() {
  test('parses without a leaders block (current deployed API shape)', () {
    final prediction = EventPrediction.fromJson(_basePrediction());

    expect(prediction.homeWinProbability, 0.62);
    expect(prediction.leaders, isNull);
  });

  test('parses a leaders block when present, preferring name over entity_id', () {
    final json = _basePrediction();
    json['leaders'] = {
      'home': {
        'passing': {'entity_id': '3139477', 'name': 'Patrick Mahomes', 'passing_yards': 267, 'passing_touchdowns': 2},
        'receiving': [
          {'entity_id': '4258', 'receiving_yards': 78, 'receiving_touchdowns': 1},
        ],
        'rushing': [],
        'sacks': [],
      },
      'away': {'passing': null, 'receiving': [], 'rushing': [], 'sacks': []},
    };

    final prediction = EventPrediction.fromJson(json);

    expect(prediction.leaders, isNotNull);
    expect(prediction.leaders!.home.passing!.displayName, 'Patrick Mahomes');
    expect(prediction.leaders!.home.receiving.single.displayName, '4258');
    expect(prediction.leaders!.home.receiving.single.stats['receiving_yards'], 78);
  });
}

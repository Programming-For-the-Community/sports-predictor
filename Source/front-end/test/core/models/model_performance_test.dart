import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/models/model_performance.dart';

Map<String, dynamic> _record({Object? history, Object? versions}) => {
      'model_name': 'win-probability',
      'version': 9,
      'kind': 'pick',
      'band_kind': 'confidence',
      'season': {'value': 0.684, 'n': 32},
      'last_period': {'label': 'Wk 3', 'value': 0.75, 'n': 12, 'version': 9},
      'periods': [
        {'label': 'Wk 3', 'value': 0.75, 'n': 12, 'version': 9},
      ],
      'bands': [],
      if (history != null) 'history': history,
      if (versions != null) 'versions': versions,
    };

void main() {
  test('reads the season history with the version behind each period, and each version\'s figure', () {
    final record = ModelPerformanceRecord.fromJson(_record(
      history: [
        {'label': 'Wk 1', 'value': 0.62, 'n': 16, 'version': 8},
        {'label': 'Wk 2', 'value': 0.69, 'n': 16, 'version': null},
      ],
      versions: [
        {'version': 8, 'value': 0.6, 'n': 16},
        {'version': 9, 'value': 0.71, 'n': 28},
      ],
    ));

    expect([for (final p in record.history) (p.label, p.value, p.version)], [('Wk 1', 0.62, 8), ('Wk 2', 0.69, null)]);
    expect([for (final v in record.versions) (v.version, v.value, v.n)], [(8, 0.6, 16), (9, 0.71, 28)]);
    expect(record.lastPeriod!.version, 9);
    expect(record.periods.single.version, 9);
  });

  test('a scorecard from before the history existed parses with none', () {
    final record = ModelPerformanceRecord.fromJson(_record());

    expect(record.history, isEmpty);
    expect(record.versions, isEmpty);
  });
}

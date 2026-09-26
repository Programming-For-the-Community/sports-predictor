import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/widgets/stat_value.dart';

void main() {
  test('sacks round to the nearest half sack', () {
    expect(statValueText('defensive_sacks', 0.13), '0');
    expect(statValueText('defensive_sacks', 0.3), '0.5');
    expect(statValueText('defensive_sacks', 0.74), '0.5');
    expect(statValueText('defensive_sacks', 0.8), '1');
    expect(statValueText('defensive_sacks', 1.5), '1.5');
    expect(statValueText('defensive_sacks', 8.6), '8.5');
  });

  test('other stats stay whole numbers', () {
    expect(statValueText('rushing_yards', 76.6), '77');
    expect(statValueText('passing_touchdowns', 1.4), '1');
  });
}

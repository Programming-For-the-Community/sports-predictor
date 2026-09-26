/// Stats credited in halves -- two players who share a sack each get half.
const _halfStatKeys = {'defensive_sacks'};

/// A player stat for display, predicted or actual: sacks to the nearest half
/// ("0.5", "2"), every other stat to the nearest whole number.
String statValueText(String statKey, double value) {
  if (_halfStatKeys.contains(statKey)) {
    final halves = (value * 2).round() / 2;
    return halves == halves.roundToDouble() ? halves.toStringAsFixed(0) : halves.toStringAsFixed(1);
  }
  return value.toStringAsFixed(0);
}

/// Touchdown stats, which show a whole number plus TdDots -- passing gets two
/// dots, rushing and receiving one.
const _touchdownDotSlots = {'passing_touchdowns': 2, 'rushing_touchdowns': 1, 'receiving_touchdowns': 1};

/// How many dots a stat's prediction gets, or null for a stat without dots.
int? tdDotSlots(String statKey) => _touchdownDotSlots[statKey];

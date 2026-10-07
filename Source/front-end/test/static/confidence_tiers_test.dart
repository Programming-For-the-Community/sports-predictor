import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/theme/app_colors.dart';
import 'package:front_end/static/confidence_tiers.dart';

void main() {
  test('looks up the tier by the favorite\'s win probability, for either team', () {
    final high = ConfidenceTier.high.minProbability;
    final med = ConfidenceTier.med.minProbability;

    expect(ConfidenceTier.forProbability(high), ConfidenceTier.high);
    expect(ConfidenceTier.forProbability(1 - high - 0.01), ConfidenceTier.high);
    expect(ConfidenceTier.forProbability(high - 0.01), ConfidenceTier.med);
    expect(ConfidenceTier.forProbability(med), ConfidenceTier.med);
    expect(ConfidenceTier.forProbability(1 - med - 0.01), ConfidenceTier.med);
    expect(ConfidenceTier.forProbability(med - 0.01), ConfidenceTier.low);
    expect(ConfidenceTier.forProbability(0.5), ConfidenceTier.low);
  });

  test('every probability falls in a tier', () {
    expect(ConfidenceTier.values.last.minProbability, 0.5);
    expect(ConfidenceTier.forProbability(1), ConfidenceTier.high);
    expect(ConfidenceTier.forProbability(0), ConfidenceTier.high);
  });

  test('each tier carries its label and color, strongest first', () {
    expect(ConfidenceTier.values.map((t) => t.label), ['HIGH', 'MED', 'LOW']);
    expect(ConfidenceTier.high.color, AppColors.cyan);
    expect(ConfidenceTier.med.color, AppColors.warn);
    expect(ConfidenceTier.low.color, AppColors.inkMute);
  });
}

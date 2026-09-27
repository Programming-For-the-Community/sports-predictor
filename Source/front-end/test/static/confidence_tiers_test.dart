import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/theme/app_colors.dart';
import 'package:front_end/static/confidence_tiers.dart';

void main() {
  test('looks up the tier by edge over a coin flip, for either team', () {
    expect(ConfidenceTier.forProbability(0.63), ConfidenceTier.high);
    expect(ConfidenceTier.forProbability(0.37), ConfidenceTier.high);
    expect(ConfidenceTier.forProbability(0.58), ConfidenceTier.med);
    expect(ConfidenceTier.forProbability(0.44), ConfidenceTier.med);
    expect(ConfidenceTier.forProbability(0.52), ConfidenceTier.low);
    expect(ConfidenceTier.forProbability(0.5), ConfidenceTier.low);
  });

  test('each tier carries its label and color, strongest first', () {
    expect(ConfidenceTier.values.map((t) => t.label), ['HIGH', 'MED', 'LOW']);
    expect(ConfidenceTier.high.color, AppColors.cyan);
    expect(ConfidenceTier.med.color, AppColors.warn);
    expect(ConfidenceTier.low.color, AppColors.inkMute);
  });
}

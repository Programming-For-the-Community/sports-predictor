import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/theme/app_colors.dart';
import 'package:front_end/core/widgets/td_dots.dart';

void main() {
  test('a full dot is green, a half dot amber, a nearly empty one red', () {
    expect(tdFillColor(1), AppColors.live);
    expect(tdFillColor(0.5), AppColors.warn);
    expect(tdFillColor(0.02), Color.lerp(AppColors.neg, AppColors.warn, 0.04));
  });

  test('the color moves steadily from red toward green as the dot fills', () {
    final greens = [0.1, 0.3, 0.5, 0.7, 0.9].map((f) => tdFillColor(f).g).toList();
    for (var i = 1; i < greens.length; i++) {
      expect(greens[i], greaterThan(greens[i - 1]));
    }
  });

  test('draws at least its slots, and more for a bigger prediction', () {
    expect(const TdDots(value: 0.4).size.width, 11);
    expect(const TdDots(value: 1.7, slots: 2).size.width, 25);
    expect(const TdDots(value: 3.2, slots: 2).size.width, 53);
  });
}

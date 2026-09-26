import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/widgets/td_dots.dart';

void main() {
  test('within 0.15 of the number shown, the lean says which way', () {
    expect(tdLeanFor(0.95), TdLean.up);
    expect(tdLeanFor(1.9), TdLean.up);
    expect(tdLeanFor(0.05), TdLean.away);
    expect(tdLeanFor(1.12), TdLean.away);
  });

  test('further out is leaning, and near the half is a toss-up', () {
    expect(tdLeanFor(0.22), TdLean.leaning);
    expect(tdLeanFor(0.8), TdLean.leaning);
    expect(tdLeanFor(0.45), TdLean.tossUp);
    expect(tdLeanFor(1.5), TdLean.tossUp);
  });

  test('draws at least its slots, and more for a bigger prediction', () {
    expect(const TdDots(value: 0.4).size.width, 11);
    expect(const TdDots(value: 1.7, slots: 2).size.width, 25);
    expect(const TdDots(value: 3.2, slots: 2).size.width, 53);
  });
}

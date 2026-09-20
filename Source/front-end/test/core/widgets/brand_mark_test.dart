import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/widgets/brand_mark.dart';

void main() {
  group('BrandMark', () {
    testWidgets('renders 3 ascending bars', (tester) async {
      await tester.pumpWidget(const MaterialApp(home: Scaffold(body: BrandMark())));

      expect(find.byType(Container), findsNWidgets(4)); // the tile itself + 3 bars
    });

    testWidgets('defaults to a 34px tile', (tester) async {
      await tester.pumpWidget(const MaterialApp(home: Scaffold(body: BrandMark())));

      final size = tester.getSize(find.byType(BrandMark));
      expect(size, const Size(34, 34));
    });

    testWidgets('scales the tile to a custom size', (tester) async {
      await tester.pumpWidget(const MaterialApp(home: Scaffold(body: BrandMark(size: 68))));

      final size = tester.getSize(find.byType(BrandMark));
      expect(size, const Size(68, 68));
    });
  });
}

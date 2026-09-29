import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/widgets/model_card_grid.dart';

void _wideView(WidgetTester tester) {
  tester.view.physicalSize = const Size(1200, 900);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
}

Widget _grid(double width, {void Function(int)? onTap}) => MaterialApp(
      home: Scaffold(
        body: SingleChildScrollView(
          child: SizedBox(
            width: width,
            child: ModelCardGrid<int>(
              items: const [1, 2, 3],
              cardBuilder: (i) => GestureDetector(
                key: ValueKey(i),
                onTap: () => onTap?.call(i),
                child: Container(color: Colors.blue, height: i * 50.0, child: Text('card $i')),
              ),
            ),
          ),
        ),
      ),
    );

void main() {
  testWidgets('cards in one row share the tallest height', (tester) async {
    _wideView(tester);
    await tester.pumpWidget(_grid(1000));

    expect(tester.getSize(find.byKey(const ValueKey(1))).height, 100);
    expect(tester.getSize(find.byKey(const ValueKey(2))).height, 100);
    expect(tester.getSize(find.byKey(const ValueKey(1))).width, 490);
  });

  testWidgets('a width change relays out the row at the new card width', (tester) async {
    _wideView(tester);
    await tester.pumpWidget(_grid(1000));
    await tester.pumpWidget(_grid(900));

    expect(tester.getSize(find.byKey(const ValueKey(1))).width, 440);
    expect(tester.getTopLeft(find.byKey(const ValueKey(2))).dx, 460);
  });

  testWidgets('taps reach the card under the pointer', (tester) async {
    _wideView(tester);
    final tapped = <int>[];
    await tester.pumpWidget(_grid(1000, onTap: tapped.add));

    await tester.tap(find.text('card 2'));

    expect(tapped, [2]);
  });

  testWidgets('a narrow width stacks cards in a single column', (tester) async {
    _wideView(tester);
    await tester.pumpWidget(_grid(400));

    expect(tester.getSize(find.byKey(const ValueKey(1))).height, 50);
    expect(tester.getTopLeft(find.byKey(const ValueKey(2))).dx, 0);
  });
}

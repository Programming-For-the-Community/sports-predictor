import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/features/season/season_horizontal_scrollable_bracket.dart';

/// A bracket taller than the viewport, inside a page that scrolls, as the
/// season page renders it.
Future<void> _pumpTallBracket(WidgetTester tester) async {
  tester.view.physicalSize = const Size(400, 800);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(const MaterialApp(
    home: Scaffold(
      body: SingleChildScrollView(
        child: HorizontalScrollableBracket(width: 1200, height: 1400, child: SizedBox(width: 1200, height: 1400)),
      ),
    ),
  ));
}

int _verticalScrollViews(WidgetTester tester) =>
    tester.widgetList<SingleChildScrollView>(find.byType(SingleChildScrollView)).where((v) => v.scrollDirection == Axis.vertical).length;

void main() {
  testWidgets('a tall bracket renders inline, so the page alone scrolls to its bottom', (tester) async {
    await _pumpTallBracket(tester);

    // Only the page's own vertical scroll -- no nested vertical pane.
    expect(_verticalScrollViews(tester), 1);
    final page = tester.state<ScrollableState>(find.byType(Scrollable).first);
    expect(page.position.maxScrollExtent, greaterThan(1400 - 800));
    expect(find.descendant(of: find.byType(HorizontalScrollableBracket), matching: find.byType(Scrollbar)), findsOneWidget);
  }, variant: TargetPlatformVariant.all());

  testWidgets('a bracket that fits across has no scrollbar', (tester) async {
    await tester.pumpWidget(const MaterialApp(
      home: Scaffold(body: HorizontalScrollableBracket(width: 300, height: 200, child: SizedBox(width: 300, height: 200))),
    ));

    expect(find.descendant(of: find.byType(HorizontalScrollableBracket), matching: find.byType(Scrollbar)), findsNothing);
  });
}

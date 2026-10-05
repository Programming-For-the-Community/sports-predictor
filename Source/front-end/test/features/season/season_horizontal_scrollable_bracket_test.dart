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
  testWidgets('a touch screen renders a tall bracket inline, so the page scrolls to its bottom', (tester) async {
    await _pumpTallBracket(tester);

    // Only the page's own vertical scroll -- no nested vertical pane to
    // trap the swipe.
    expect(_verticalScrollViews(tester), 1);
    final page = tester.state<ScrollableState>(find.byType(Scrollable).first);
    expect(page.position.maxScrollExtent, greaterThan(1400 - 800));
  }, variant: const TargetPlatformVariant({TargetPlatform.android, TargetPlatform.iOS}));

  testWidgets('a desktop keeps the capped pane with its pinned scrollbar', (tester) async {
    await _pumpTallBracket(tester);

    expect(_verticalScrollViews(tester), 2);
  }, variant: const TargetPlatformVariant({TargetPlatform.windows, TargetPlatform.macOS}));
}

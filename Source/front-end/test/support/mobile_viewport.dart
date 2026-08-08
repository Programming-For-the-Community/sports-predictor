import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

/// Narrowest mainstream Android/iPhone logical widths -- where a fixed-
/// width card too wide for the screen (see core/widgets/responsive.dart)
/// would overflow first.
const mobileViewportWidths = [360.0, 375.0, 390.0];

/// Pumps `widget` at a specific viewport width/height and restores the
/// test binding's real size afterward. Height is fixed and generous
/// (this project's pages scroll vertically; only width ever needs to be
/// tight) so every call only has one meaningful dimension to vary.
Future<void> pumpAtWidth(WidgetTester tester, double width, Widget widget) async {
  tester.view.physicalSize = Size(width, 1200);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(widget);
  await tester.pumpAndSettle();
}

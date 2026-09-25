import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

/// Narrowest mainstream Android/iPhone logical widths -- where a fixed-
/// width card too wide for the screen (see core/widgets/responsive.dart)
/// would overflow first.
const mobileViewportWidths = [360.0, 375.0, 390.0];

/// Pumps `widget` at a specific viewport width on a realistic portrait-
/// phone height (700), with the system text scale at 1.5 -- a common
/// accessibility setting that pushes fixed-geometry layouts past their
/// bounds while every default-scale test still passes. Restores the test
/// binding's real size and text scale afterward.
Future<void> pumpAtWidth(WidgetTester tester, double width, Widget widget) async {
  tester.view.physicalSize = Size(width, 700);
  tester.platformDispatcher.textScaleFactorTestValue = 1.5;
  addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(widget);
  await tester.pumpAndSettle();
}

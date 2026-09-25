import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

/// Narrowest mainstream Android/iPhone logical widths -- where a fixed-
/// width card too wide for the screen (see core/widgets/responsive.dart)
/// would overflow first.
const mobileViewportWidths = [360.0, 375.0, 390.0];

// Percent; override with --dart-define=TEXT_SCALE_PCT=100 for the default-scale result.
final _textScale = const int.fromEnvironment('TEXT_SCALE_PCT', defaultValue: 150) / 100;

bool _fontsLoaded = false;

/// Registers the app's real fonts (test/fonts -- the exact files google_fonts
/// fetches at runtime, both OFL) under the family names google_fonts gives
/// them, so text is laid out at real glyph widths instead of the test
/// binding's default 1em-wide boxes. Without this every string looks about
/// twice as wide as on a phone.
Future<void> loadAppFonts() async {
  if (_fontsLoaded) return;
  _fontsLoaded = true;
  const files = {
    'SpaceGrotesk_regular': 'SpaceGrotesk-400',
    'SpaceGrotesk_500': 'SpaceGrotesk-500',
    'SpaceGrotesk_600': 'SpaceGrotesk-600',
    'SpaceGrotesk_700': 'SpaceGrotesk-700',
    'IBMPlexMono_regular': 'IBMPlexMono-400',
    'IBMPlexMono_500': 'IBMPlexMono-500',
    'IBMPlexMono_600': 'IBMPlexMono-600',
    'IBMPlexMono_700': 'IBMPlexMono-700',
  };
  for (final entry in files.entries) {
    final bytes = await File('test/fonts/${entry.value}.ttf').readAsBytes();
    final loader = FontLoader(entry.key)..addFont(Future.value(ByteData.sublistView(bytes)));
    await loader.load();
  }
}

/// Pumps `widget` at a specific viewport width on a realistic portrait-
/// phone height (700), with the system text scale at 1.5 -- a common
/// accessibility setting that pushes fixed-geometry layouts past their
/// bounds while every default-scale test still passes. Restores the test
/// binding's real size and text scale afterward.
Future<void> pumpAtWidth(WidgetTester tester, double width, Widget widget) async {
  await tester.runAsync(loadAppFonts);
  tester.view.physicalSize = Size(width, 700);
  tester.platformDispatcher.textScaleFactorTestValue = _textScale;
  addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(widget);
  await tester.pumpAndSettle();
  expect(truncatedText(tester), isEmpty, reason: 'text clipped with an ellipsis at ${width}px');
}

/// Text of every rendered paragraph that was cut off by its maxLines (an
/// ellipsis). Overflow exceptions never fire for these -- the widget
/// handles its own overflow -- so takeException() alone can't see a
/// clipped name or stat line. Only meaningful with loadAppFonts() applied
/// (pumpAtWidth does that).
List<String> truncatedText(WidgetTester tester) {
  final clipped = <String>[];
  void visit(RenderObject node) {
    if (node is RenderParagraph && node.didExceedMaxLines) clipped.add(node.text.toPlainText());
    node.visitChildren(visit);
  }
  visit(tester.binding.renderViews.first);
  return clipped;
}

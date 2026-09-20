import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/theme/app_colors.dart';
import 'package:front_end/core/theme/app_theme.dart';

void main() {
  group('AppTheme.dark', () {
    testWidgets('wires the app color tokens into the scaffold/color scheme', (tester) async {
      final theme = AppTheme.dark;

      expect(theme.scaffoldBackgroundColor, AppColors.bg);
      expect(theme.colorScheme.surface, AppColors.bg);
      expect(theme.colorScheme.primary, AppColors.cyan);
      expect(theme.colorScheme.secondary, AppColors.violet);
      expect(theme.colorScheme.error, AppColors.neg);
    });

    testWidgets('is a Material 3 dark theme', (tester) async {
      expect(AppTheme.dark.useMaterial3, isTrue);
      expect(AppTheme.dark.brightness, Brightness.dark);
    });

    testWidgets('applies the ink color to the text theme', (tester) async {
      final theme = AppTheme.dark;

      expect(theme.textTheme.bodyMedium?.color, AppColors.ink);
      expect(theme.textTheme.displayMedium?.color, AppColors.ink);
    });

    testWidgets('gives the app bar a transparent, flat surface', (tester) async {
      final appBarTheme = AppTheme.dark.appBarTheme;

      expect(appBarTheme.backgroundColor, Colors.transparent);
      expect(appBarTheme.elevation, 0);
    });

    testWidgets('styles elevated buttons with the cyan accent', (tester) async {
      final style = AppTheme.dark.elevatedButtonTheme.style;

      expect(style?.backgroundColor?.resolve({}), AppColors.cyan);
      expect(style?.foregroundColor?.resolve({}), AppColors.bg);
    });
  });
}

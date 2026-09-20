import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/theme/app_colors.dart';
import 'package:front_end/core/widgets/status_toggle.dart';

void main() {
  group('StatusToggle', () {
    testWidgets('renders the label', (tester) async {
      await tester.pumpWidget(MaterialApp(
        home: Scaffold(body: StatusToggle(label: 'Upcoming', selected: false, onTap: () {}, accentColor: AppColors.cyan)),
      ));

      expect(find.text('Upcoming'), findsOneWidget);
    });

    testWidgets('calls onTap when tapped', (tester) async {
      var tapped = false;
      await tester.pumpWidget(MaterialApp(
        home: Scaffold(body: StatusToggle(label: 'Upcoming', selected: false, onTap: () => tapped = true, accentColor: AppColors.cyan)),
      ));

      await tester.tap(find.byType(StatusToggle));

      expect(tapped, isTrue);
    });

    testWidgets('selected uses the accent color, unselected uses the muted color', (tester) async {
      await tester.pumpWidget(MaterialApp(
        home: Scaffold(body: StatusToggle(label: 'Upcoming', selected: true, onTap: () {}, accentColor: AppColors.violet)),
      ));

      final text = tester.widget<Text>(find.text('Upcoming'));
      expect(text.style?.color, AppColors.violet);
    });
  });

  group('StatusToggleLabels', () {
    test('exposes the two shared labels', () {
      expect(StatusToggleLabels.upcoming, 'Upcoming/Current');
      expect(StatusToggleLabels.completed, 'Completed');
    });
  });
}

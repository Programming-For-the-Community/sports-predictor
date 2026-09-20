import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/widgets/points_standings_table.dart';

class _Standing {
  const _Standing({required this.name, required this.current, required this.projected, required this.champion});
  final String name;
  final double current;
  final double projected;
  final double champion;
}

void main() {
  group('formatPoints', () {
    test('shows one decimal place below 1000', () {
      expect(formatPoints(347.5), '347.5');
    });

    test('rounds to a whole number at and above 1000', () {
      expect(formatPoints(1234.6), '1235');
      expect(formatPoints(1000.0), '1000');
    });
  });

  group('formatPercent', () {
    test('rounds a fraction to a whole-number percent', () {
      expect(formatPercent(0.653), '65%');
    });
  });

  group('PercentText', () {
    testWidgets('renders the formatted percent', (tester) async {
      await tester.pumpWidget(const MaterialApp(home: Scaffold(body: PercentText(0.8))));

      expect(find.text('80%'), findsOneWidget);
    });
  });

  group('StandingsHeaderRow', () {
    testWidgets('renders every label', (tester) async {
      await tester.pumpWidget(const MaterialApp(
        home: Scaffold(body: StandingsHeaderRow(labels: ['#', 'DRIVER', 'POINTS', 'CHAMP%'], flexes: [1, 4, 2, 2])),
      ));

      for (final label in ['#', 'DRIVER', 'POINTS', 'CHAMP%']) {
        expect(find.text(label), findsOneWidget);
      }
    });
  });

  group('PointsStandingsTable', () {
    final standings = [
      const _Standing(name: 'Max Verstappen', current: 350.0, projected: 420.5, champion: 0.82),
      const _Standing(name: 'Lando Norris', current: 300.0, projected: 380.0, champion: 0.15),
    ];

    Widget wrap(List<_Standing> data) => MaterialApp(
          home: Scaffold(
            body: PointsStandingsTable<_Standing>(
              standings: data,
              nameColumnLabel: 'DRIVER',
              displayName: (s) => s.name,
              currentPoints: (s) => s.current,
              projectedPoints: (s) => s.projected,
              championProbability: (s) => s.champion,
            ),
          ),
        );

    testWidgets('renders the header plus one row per standing, 1-indexed', (tester) async {
      await tester.pumpWidget(wrap(standings));

      expect(find.text('DRIVER'), findsOneWidget);
      expect(find.text('1'), findsOneWidget);
      expect(find.text('2'), findsOneWidget);
      expect(find.text('Max Verstappen'), findsOneWidget);
      expect(find.text('Lando Norris'), findsOneWidget);
    });

    testWidgets('renders current and projected points for each row', (tester) async {
      await tester.pumpWidget(wrap(standings));

      expect(find.text('350.0'), findsOneWidget);
      expect(find.text('420.5'), findsOneWidget);
      expect(find.text('300.0'), findsOneWidget);
      expect(find.text('380.0'), findsOneWidget);
    });

    testWidgets('renders championship probability as a percent', (tester) async {
      await tester.pumpWidget(wrap(standings));

      expect(find.text('82%'), findsOneWidget);
      expect(find.text('15%'), findsOneWidget);
    });

    testWidgets('renders just the header, no rows, for an empty standings list', (tester) async {
      await tester.pumpWidget(wrap(const []));

      expect(find.text('DRIVER'), findsOneWidget);
      expect(find.byType(Divider), findsNothing);
    });
  });
}

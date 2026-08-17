import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/static/nfl_team_colors.dart';

void main() {
  group('teamDisplayFor', () {
    test('NFL prefers its own static table over apiColor', () {
      final info = teamDisplayFor('nfl', '12', 'XYZ', apiColor: 'c8102e');

      expect(info.abbreviation, 'KC');
      expect(info.primary, isNot(const Color(0xFFC8102E)));
    });

    test('a non-NFL sport parses a bare 6-digit hex apiColor', () {
      final info = teamDisplayFor('nba', '2', 'BOS', apiColor: 'c8102e');

      expect(info.primary, const Color(0xFFC8102E));
    });

    test('a "#"-prefixed apiColor still parses', () {
      final info = teamDisplayFor('nba', '2', 'BOS', apiColor: '#00FF00');

      expect(info.primary, const Color(0xFF00FF00));
    });

    test('a missing apiColor falls back to no dot, not an error', () {
      final info = teamDisplayFor('nba', '2', 'BOS');

      expect(info.primary, isNull);
      expect(info.abbreviation, 'BOS');
    });

    test('a malformed apiColor degrades to no dot rather than throwing', () {
      final info = teamDisplayFor('nba', '2', 'BOS', apiColor: 'not-a-color');

      expect(info.primary, isNull);
    });
  });
}

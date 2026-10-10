import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/mobile/model_report.dart';
import 'package:front_end/core/mobile/widget_data.dart';
import 'package:front_end/core/models/sport_config.dart';
import 'package:front_end/static/model_display.dart';
import 'package:front_end/static/prop_benchmarks.dart';

void main() {
  group('hockey model display', () {
    test('each prop model has its own unit', () {
      expect(modelDisplay('player-prop-goals').valueUnit, 'goals');
      expect(modelDisplay('player-prop-goals-against').valueUnit, 'GA');
      expect(modelDisplay('player-prop-shots-total').valueUnit, 'SOG');
      expect(modelDisplay('player-prop-blocked-shots').valueUnit, 'blk');
      expect(modelDisplay('player-prop-hits').valueUnit, 'hits');
      expect(modelDisplay('player-prop-saves').valueUnit, 'saves');
    });

    test('a low-count stat keeps a decimal in its range and a high-count one does not', () {
      expect(modelDisplay('player-prop-goals').rangeDecimals, 1);
      expect(modelDisplay('player-prop-saves').rangeDecimals, 0);
      expect(modelDisplay('player-prop-saves').countNoun, 'players');
    });

    test('basketball and football units are unchanged', () {
      expect(modelDisplay('player-prop-assists').valueUnit, 'ast');
      expect(modelDisplay('player-prop-passing-touchdowns').valueUnit, 'TDs');
    });
  });

  group('NHL in the per-sport maps', () {
    test('has a Performance tab and stays hidden until activated', () {
      final nhl = sportById(SportIds.nhl);
      expect(nhl.hasPerformanceTab, isTrue);
      expect(nhl.hasSeasonProjection, isTrue);
      expect(nhl.active, isFalse);
    });

    test('player-props widgets list goals, shots on goal and saves', () {
      final stats = propStatsBySport[SportIds.nhl]!;
      expect(stats.keys, ['goals', 'shots_total', 'saves']);
      expect(propModelName('shots_total'), 'player-prop-shots-total');
      expect(stats['saves']!.decimals, 0);
    });

    test('has a halftime offset and a weekly report day', () {
      expect(halftimeAfterStart[SportIds.nhl], const Duration(minutes: 75));
      expect(reportWeekday[SportIds.nhl], DateTime.sunday);
    });
  });
}

import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/mobile/model_report.dart';
import 'package:front_end/core/models/model_performance.dart';
import 'package:front_end/core/models/sport_config.dart';

import '../../support/model_performance_fixtures.dart';

ModelPerformance _performance(List<ModelPerformanceRecord> models) =>
    ModelPerformance(sport: 'nfl', season: 2026, periodKind: 'week', models: models);

void main() {
  group('easternOffsetHours', () {
    test('is 4 during daylight time and 5 otherwise, switching at 2 AM local', () {
      // 2026: DST runs from Sunday Mar 8 to Sunday Nov 1.
      expect(easternOffsetHours(DateTime.utc(2026, 3, 8, 6, 59)), 5);
      expect(easternOffsetHours(DateTime.utc(2026, 3, 8, 7)), 4);
      expect(easternOffsetHours(DateTime.utc(2026, 11, 1, 5, 59)), 4);
      expect(easternOffsetHours(DateTime.utc(2026, 11, 1, 6)), 5);
      expect(easternOffsetHours(DateTime.utc(2027, 1, 15)), 5);
    });
  });

  group('latestReportSlot', () {
    test("is 10 AM Eastern on the sport's report day", () {
      // 2026-10-07 is a Wednesday -- NFL's schedule-sync day, under daylight time.
      expect(latestReportSlot(SportIds.nfl, DateTime.utc(2026, 10, 7, 14)), DateTime.utc(2026, 10, 7, 14));
      // 2026-12-02 is a Wednesday under standard time.
      expect(latestReportSlot(SportIds.nfl, DateTime.utc(2026, 12, 2, 15)), DateTime.utc(2026, 12, 2, 15));
    });

    test("is last week's slot before 10 AM Eastern on the report day", () {
      expect(latestReportSlot(SportIds.nfl, DateTime.utc(2026, 10, 7, 13, 59)), DateTime.utc(2026, 9, 30, 14));
    });

    test('uses the Eastern date, not the UTC one', () {
      // 01:00 UTC Thursday is still Wednesday evening Eastern.
      expect(latestReportSlot(SportIds.nfl, DateTime.utc(2026, 10, 8, 1)), DateTime.utc(2026, 10, 7, 14));
    });

    test('keeps 10 AM Eastern across the November clock change', () {
      // PGA reports Mondays; Nov 2 2026 is the Monday after DST ends.
      expect(latestReportSlot(SportIds.pga, DateTime.utc(2026, 11, 3)), DateTime.utc(2026, 11, 2, 15));
    });

    test('is the most recent report day earlier in the week', () {
      // Saturday 2026-10-10: PGA reports Mondays, F1 Tuesdays.
      final saturday = DateTime.utc(2026, 10, 10, 15);
      expect(latestReportSlot(SportIds.pga, saturday), DateTime.utc(2026, 10, 5, 14));
      expect(latestReportSlot(SportIds.f1, saturday), DateTime.utc(2026, 10, 6, 14));
      expect(latestReportSlot(SportIds.ncaambb, saturday), DateTime.utc(2026, 10, 10, 14));
    });

    test('every reporting sport has its own weekday', () {
      expect(reportWeekday.values.toSet(), hasLength(reportWeekday.length));
    });
  });

  group('isReportDue', () {
    final slot = DateTime.utc(2026, 10, 7, 14);

    test('is due when never reported', () {
      expect(isReportDue(slot: slot, lastReportedSlot: null, nowUtc: slot.add(const Duration(hours: 1))), isTrue);
    });

    test('is not due once this slot is reported', () {
      expect(isReportDue(slot: slot, lastReportedSlot: slot, nowUtc: slot.add(const Duration(hours: 1))), isFalse);
    });

    test('is due when only last week\'s slot was reported', () {
      final lastWeek = slot.subtract(const Duration(days: 7));
      expect(isReportDue(slot: slot, lastReportedSlot: lastWeek, nowUtc: slot.add(const Duration(hours: 1))), isTrue);
    });

    test('is skipped rather than sent late past the grace period', () {
      expect(isReportDue(slot: slot, lastReportedSlot: null, nowUtc: slot.add(reportGracePeriod)), isFalse);
    });
  });

  group('buildModelReport', () {
    final nfl = sportById(SportIds.nfl);

    test('leads with pick models, then amounts, in the scorecard\'s own units', () {
      final report = buildModelReport(nfl, _performance([amountRecord(), pickRecord()]))!;

      expect(report.periodLabel, 'Wk 3');
      expect(report.title, 'NFL · Wk 3 model report');
      expect(report.body, 'Win Probability: 75% (9/12) · Score Margin: avg miss 8.9 pts');
    });

    test('lists at most three models and counts the rest', () {
      final report = buildModelReport(
        nfl,
        _performance([
          pickRecord(),
          amountRecord(),
          amountRecord(modelName: 'home-score'),
          amountRecord(modelName: 'away-score'),
          amountRecord(modelName: 'player-prop-passing-yards'),
        ]),
      )!;

      expect(report.body, endsWith('· +2 more'));
      expect(report.body.split(' · '), hasLength(4));
    });

    test('is null when nothing was graded in the last period', () {
      expect(buildModelReport(nfl, _performance([pickRecord(last: null), amountRecord(last: null)])), isNull);
      expect(buildModelReport(nfl, _performance(const [])), isNull);
    });
  });
}

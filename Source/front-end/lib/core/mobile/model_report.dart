import '../../static/model_display.dart';
import '../models/model_performance.dart';
import '../models/sport_config.dart';
import '../widgets/model_performance_format.dart';

/// Each sport's weekly model report goes out at 10 AM Eastern on its
/// schedule-sync day (Terraform/scheduler-*-schedule-sync.tf runs at 6 AM
/// Eastern that same day). PGA's sync is daily, so its report follows the
/// Sunday finish; F1 has no sync and reports on Tuesday; NHL's sync is daily
/// too and reports on Sunday.
const reportWeekday = {
  SportIds.nfl: DateTime.wednesday,
  SportIds.ncaafb: DateTime.thursday,
  SportIds.nba: DateTime.friday,
  SportIds.ncaambb: DateTime.saturday,
  SportIds.pga: DateTime.monday,
  SportIds.f1: DateTime.tuesday,
  SportIds.nhl: DateTime.sunday,
};

const reportHourEastern = 10;

/// A slot older than this is skipped rather than reported late.
const reportGracePeriod = Duration(days: 2);

/// Hours US Eastern is behind UTC at [utc]: 4 under daylight time (2 AM
/// local on the second Sunday in March to 2 AM local on the first Sunday in
/// November), otherwise 5.
int easternOffsetHours(DateTime utc) {
  final dstStart = DateTime.utc(utc.year, 3, _nthSunday(utc.year, 3, 2), 7);
  final dstEnd = DateTime.utc(utc.year, 11, _nthSunday(utc.year, 11, 1), 6);
  return (!utc.isBefore(dstStart) && utc.isBefore(dstEnd)) ? 4 : 5;
}

int _nthSunday(int year, int month, int n) {
  final first = DateTime.utc(year, month, 1);
  return 1 + (DateTime.sunday - first.weekday) % 7 + 7 * (n - 1);
}

/// [day]'s date (its UTC fields read as an Eastern calendar date) at [hour] Eastern, as UTC.
DateTime _easternToUtc(DateTime day, int hour) {
  final standardTime = DateTime.utc(day.year, day.month, day.day, hour + 5);
  return DateTime.utc(day.year, day.month, day.day, hour + easternOffsetHours(standardTime));
}

/// The most recent report slot at or before [nowUtc], as UTC.
DateTime latestReportSlot(String sportId, DateTime nowUtc) {
  final local = nowUtc.subtract(Duration(hours: easternOffsetHours(nowUtc)));
  final today = DateTime.utc(local.year, local.month, local.day);
  final day = today.subtract(Duration(days: (today.weekday - reportWeekday[sportId]!) % 7));
  final slot = _easternToUtc(day, reportHourEastern);
  return slot.isAfter(nowUtc) ? _easternToUtc(day.subtract(const Duration(days: 7)), reportHourEastern) : slot;
}

bool isReportDue({required DateTime slot, required DateTime? lastReportedSlot, required DateTime nowUtc}) =>
    (lastReportedSlot == null || lastReportedSlot.isBefore(slot)) && nowUtc.difference(slot) < reportGracePeriod;

class ModelReport {
  const ModelReport({required this.periodLabel, required this.title, required this.body});

  /// The scorecard's last-period label ("Wk 5", an event name) -- a report
  /// is only sent once per label.
  final String periodLabel;
  final String title;
  final String body;
}

const _maxModelsInBody = 3;

/// Null when no model was graded in the last period (off-season, or the
/// scorecard hasn't caught up).
ModelReport? buildModelReport(SportConfig sport, ModelPerformance performance) {
  final graded = performance.models.where((m) => (m.lastPeriod?.n ?? 0) > 0 && m.lastPeriod?.value != null).toList()
    ..sort((a, b) => _kindOrder(a.kind).compareTo(_kindOrder(b.kind)));
  if (graded.isEmpty) return null;
  final label = graded.first.lastPeriod!.label ?? '';
  final lines = graded.take(_maxModelsInBody).map(_line).toList();
  if (graded.length > _maxModelsInBody) lines.add('+${graded.length - _maxModelsInBody} more');
  return ModelReport(
    periodLabel: label,
    title: label.isEmpty ? '${sport.displayName} model report' : '${sport.displayName} · $label model report',
    body: lines.join(' · '),
  );
}

int _kindOrder(String kind) => switch (kind) {
      ModelPerformanceRecord.kindPick => 0,
      ModelPerformanceRecord.kindChance => 1,
      _ => 2,
    };

String _line(ModelPerformanceRecord record) {
  final window = record.lastPeriod!;
  final name = modelDisplayName(record.modelName);
  if (record.isAmount) {
    final display = modelDisplay(record.modelName);
    final unit = display.valueUnit.isEmpty ? '' : ' ${display.valueUnit}';
    return '$name: avg miss ${window.value!.toStringAsFixed(display.missDecimals)}$unit';
  }
  final hits = (window.value! * window.n).round();
  return '$name: ${percent(window.value!, decimals: 0)} ($hits/${window.n})';
}

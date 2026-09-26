import '../../static/model_display.dart';
import '../models/model_performance.dart';

/// Pure formatting for the Performance cards -- kept out of the widget so the
/// wording (which is most of what a non-technical reader sees) is unit tested
/// directly.

enum DeltaTone { good, bad, flat }

class DeltaText {
  const DeltaText(this.text, this.tone);
  final String text;
  final DeltaTone tone;
}

String percent(double value, {int decimals = 1}) => '${(value * 100).toStringAsFixed(decimals)}%';

String _amount(double value, int decimals) => value.toStringAsFixed(decimals);

/// The headline number without its unit: "68.4%" for a pick, "58.2" for an
/// amount (the unit is styled separately by the card).
String headlineNumber(ModelPerformanceRecord record, ModelDisplay display, double value) =>
    record.isAmount ? _amount(value, display.missDecimals) : percent(value);

/// "ACCURACY" for a pick or chance model, "AVG MISS" for an amount model.
String headlineLabel(ModelPerformanceRecord record) => record.isAmount ? 'AVG MISS' : 'ACCURACY';

/// How last period compares with the season. For a pick, higher accuracy is
/// better; for an amount, a smaller average miss is.
DeltaText? describeDelta(ModelPerformanceRecord record, ModelDisplay display) {
  final last = record.lastPeriod?.value;
  final season = record.season.value;
  if (last == null || season == null) return null;

  if (!record.isAmount) {
    final points = (last - season) * 100;
    if (points.abs() < 0.05) return const DeltaText('same as season', DeltaTone.flat);
    final sign = points > 0 ? '+' : '-';
    return DeltaText('$sign${points.abs().toStringAsFixed(1)} pts vs season', points > 0 ? DeltaTone.good : DeltaTone.bad);
  }

  final diff = last - season;
  final half = 0.5 / _pow10(display.missDecimals);
  if (diff.abs() < half) return const DeltaText('same as season', DeltaTone.flat);
  final size = '${_amount(diff.abs(), display.missDecimals)}${display.valueUnit.isEmpty ? '' : ' ${display.valueUnit}'}';
  return diff < 0 ? DeltaText('$size closer', DeltaTone.good) : DeltaText('$size further off', DeltaTone.bad);
}

num _pow10(int exponent) {
  var result = 1;
  for (var i = 0; i < exponent; i++) {
    result *= 10;
  }
  return result;
}

/// "+14% BETTER" / "-3% WORSE" against the naive baseline, null when unknown.
String? vsBaselineText(ModelPerformanceRecord record) {
  final value = record.vsBaselinePct;
  if (value == null) return null;
  final rounded = value.round();
  return '${rounded >= 0 ? '+' : ''}$rounded% ${rounded >= 0 ? 'BETTER' : 'WORSE'}';
}

/// The model's own figure from training, formatted like the headline.
String? atTrainingText(ModelPerformanceRecord record, ModelDisplay display) {
  final value = record.atTraining;
  if (value == null) return null;
  return record.isAmount ? withUnit(_amount(value, display.missDecimals), display) : percent(value);
}

String withUnit(String number, ModelDisplay display) => display.valueUnit.isEmpty ? number : '$number ${display.valueUnit}';

/// Section title above a card's bands.
String bandTitle(ModelPerformanceRecord record) => switch (record.bandKind) {
      ModelPerformanceRecord.bandKindConfidence => 'CONFIDENCE',
      ModelPerformanceRecord.bandKindChance => 'PREDICTED CHANCE',
      _ => 'PREDICTED AMOUNT',
    };

/// One line saying what the tiers mean for this model.
String bandCaption(ModelPerformanceRecord record, ModelDisplay display) {
  if (record.bandKind == ModelPerformanceRecord.bandKindConfidence) {
    return 'How far our win chance was from 50/50. ${_confidenceTiers(record)}.';
  }
  if (record.bandKind == ModelPerformanceRecord.bandKindChance) {
    return 'The ${chanceNoun(record.modelName)} chance we gave each ${_singular(nounFor(record, display))}.';
  }
  final noun = nounFor(record, display)[0].toUpperCase() + nounFor(record, display).substring(1);
  return '$noun grouped into equal thirds, from the lowest to the highest amount we predicted this season.';
}

/// "HIGH is 63%+, MED 56–63%, LOW under 56%" -- from the tiers' own edges, so
/// it can never disagree with how the backend grouped the predictions.
String _confidenceTiers(ModelPerformanceRecord record) {
  final byTag = {for (final band in record.bands) band.tag: band};
  int? winChance(String tag) {
    final edge = byTag[tag]?.lo;
    return edge == null ? null : ((0.5 + edge) * 100).round();
  }

  final high = winChance('HIGH');
  final med = winChance('MED');
  if (high == null || med == null) return 'Higher tiers mean a bigger edge over a coin flip';
  return 'HIGH is $high%+, MED $med–$high%, LOW under $med%';
}

/// The line under the bars saying what a bar measures.
String barCaption(ModelPerformanceRecord record, ModelDisplay display) {
  if (record.isPick) return 'Share of picks that were right.';
  if (!record.isAmount) return 'Share we called right (yes if we gave over 50%, otherwise no).';
  final margin = record.marginOfError;
  if (margin == null) return 'Share that landed close to our number.';
  return 'Share that landed within ±${withUnit(_amount(margin, display.missDecimals), display)} of our number.';
}

/// "120–193 yds" for an amount band; null for a band with no range (a pick tier).
String? bandRangeText(PerformanceBand band, ModelPerformanceRecord record, ModelDisplay display) {
  if (record.bandKind == ModelPerformanceRecord.bandKindConfidence || band.lo == null || band.hi == null) return null;
  if (record.bandKind == ModelPerformanceRecord.bandKindChance) return '${(band.lo! * 100).round()}–${(band.hi! * 100).round()}%';
  final lo = _amount(band.lo!, display.rangeDecimals);
  final hi = _amount(band.hi!, display.rangeDecimals);
  return withUnit('$lo–$hi', display);
}

/// Whether the model tended to miss high (predicted above what happened) or low,
/// worded for a reader: "Missed high by 4.2 yds on average". A lean smaller than
/// a tenth of the model's usual miss is not a lean at all ("No clear lean").
/// Null for a pick model, or when there is nothing to say yet.
String? leanText(double? bias, ModelPerformanceRecord record, ModelDisplay display) {
  if (!record.isAmount || bias == null) return null;
  final margin = record.marginOfError ?? 0;
  final smallest = margin > 0 ? margin * 0.1 : 0.5 / _pow10(display.missDecimals);
  if (bias.abs() < smallest) return 'No clear lean';
  final size = withUnit(_amount(bias.abs(), display.missDecimals), display);
  return 'Missed ${bias > 0 ? 'high' : 'low'} by $size on average';
}

/// The season-level version for the facts row: "High by 2.1 yds" / "Low by ..." / "Neither".
String? seasonLeanText(ModelPerformanceRecord record, ModelDisplay display) {
  final text = leanText(record.bias, record, display);
  if (text == null) return null;
  if (text == 'No clear lean') return 'Neither';
  final direction = record.bias! > 0 ? 'High' : 'Low';
  return '$direction by ${withUnit(_amount(record.bias!.abs(), display.missDecimals), display)}';
}

/// "9 games", "1 game", "402 golfers" -- singularizes the count noun at 1.
String countText(int n, String noun) {
  if (n != 1) return '$n $noun';
  const singular = {'games': 'game', 'teams': 'team', 'players': 'player', 'golfers': 'golfer', 'drivers': 'driver'};
  return '$n ${singular[noun] ?? noun}';
}

/// A recent-period chip's value: a rounded percent for a pick, the average miss for an amount.
String periodValueText(ModelPerformanceRecord record, ModelDisplay display, double value) =>
    record.isAmount ? _amount(value, display.missDecimals) : percent(value, decimals: 0);

/// What one graded prediction is called: the sport's own word when it sent one,
/// else the app's per-model default.
String nounFor(ModelPerformanceRecord record, ModelDisplay display) => record.countNoun ?? display.countNoun;

String _singular(String noun) => noun.endsWith('s') ? noun.substring(0, noun.length - 1) : noun;

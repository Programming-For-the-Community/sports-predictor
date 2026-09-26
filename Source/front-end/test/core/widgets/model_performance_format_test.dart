import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/widgets/model_performance_format.dart';
import 'package:front_end/static/model_display.dart';

import '../../support/model_performance_fixtures.dart';

void main() {
  final pickDisplay = modelDisplay('win-probability');
  final yardsDisplay = modelDisplay('player-prop-passing-yards');
  final marginDisplay = modelDisplay('score-margin');

  group('headline', () {
    test('a pick shows accuracy as a percent, an amount shows the average miss', () {
      expect(headlineLabel(pickRecord()), 'ACCURACY');
      expect(headlineNumber(pickRecord(), pickDisplay, 0.684), '68.4%');
      expect(headlineLabel(amountRecord()), 'AVG MISS');
      expect(headlineNumber(amountRecord(modelName: 'player-prop-passing-yards'), yardsDisplay, 58.2), '58.2');
    });
  });

  group('describeDelta', () {
    test('a pick that improved is good, with the change in points', () {
      final delta = describeDelta(pickRecord(season: 0.684, last: 0.75), pickDisplay)!;

      expect(delta.text, '+6.6 pts vs season');
      expect(delta.tone, DeltaTone.good);
    });

    test('a pick that slipped is bad', () {
      final delta = describeDelta(pickRecord(season: 0.75, last: 0.6), pickDisplay)!;

      expect(delta.text, '-15.0 pts vs season');
      expect(delta.tone, DeltaTone.bad);
    });

    test('an amount with a smaller miss is closer (good); a larger one is further off (bad)', () {
      final closer = describeDelta(amountRecord(season: 10.2, last: 8.9), marginDisplay)!;
      final further = describeDelta(amountRecord(modelName: 'player-prop-passing-yards', season: 58.2, last: 61.4), yardsDisplay)!;

      expect(closer.text, '1.3 pts closer');
      expect(closer.tone, DeltaTone.good);
      expect(further.text, '3.2 yds further off');
      expect(further.tone, DeltaTone.bad);
    });

    test('no meaningful change reads as the same as the season', () {
      expect(describeDelta(pickRecord(season: 0.75, last: 0.7501), pickDisplay)!.tone, DeltaTone.flat);
      expect(describeDelta(amountRecord(season: 0.9, last: 0.9), marginDisplay)!.text, 'same as season');
    });

    test('there is nothing to compare without a last period', () {
      expect(describeDelta(pickRecord(last: null), pickDisplay), isNull);
    });
  });

  group('facts', () {
    test('vs baseline reads BETTER or WORSE with a rounded percent', () {
      expect(vsBaselineText(pickRecord(vsBaselinePct: 14.4)), '+14% BETTER');
      expect(vsBaselineText(pickRecord(vsBaselinePct: -3.2)), '-3% WORSE');
      expect(vsBaselineText(pickRecord(vsBaselinePct: null)), isNull);
    });

    test('at training is formatted like the headline, with its unit', () {
      expect(atTrainingText(pickRecord(atTraining: 0.661), pickDisplay), '66.1%');
      expect(atTrainingText(amountRecord(modelName: 'player-prop-passing-yards', atTraining: 57.5), yardsDisplay), '57.5 yds');
      expect(atTrainingText(pickRecord(atTraining: null), pickDisplay), isNull);
    });
  });

  group('confidence wording', () {
    test('only a pick model says confidence; amounts say predicted amount', () {
      expect(bandTitle(pickRecord()), 'CONFIDENCE');
      expect(bandTitle(amountRecord()), 'PREDICTED AMOUNT');
    });

    test("the tiers are spelled out from the backend's own edges", () {
      expect(
        bandCaption(pickRecord(), pickDisplay),
        'How far our win chance was from 50/50. HIGH is 63%+, MED 56–63%, LOW under 56%.',
      );
    });

    test('an amount caption names what is being counted', () {
      expect(bandCaption(amountRecord(modelName: 'player-prop-sacks'), modelDisplay('player-prop-sacks')), startsWith('Players grouped into equal thirds'));
      expect(bandCaption(amountRecord(), marginDisplay), startsWith('Games grouped into equal thirds'));
      expect(bandCaption(amountRecord(modelName: 'home-score'), modelDisplay('home-score')), startsWith('Teams grouped into equal thirds'));
    });

    test('the bar line says a pick bar is picks that were right, an amount bar is within the margin of error', () {
      expect(barCaption(pickRecord(), pickDisplay), 'Share of picks that were right.');
      expect(barCaption(amountRecord(marginOfError: 10.8), marginDisplay), 'Share that landed within ±10.8 pts of our number.');
      expect(
        barCaption(amountRecord(modelName: 'player-prop-passing-yards', marginOfError: 57.5), yardsDisplay),
        'Share that landed within ±57.5 yds of our number.',
      );
    });
  });

  group('rows', () {
    test("an amount band shows its range in the stat's units; a pick tier has none", () {
      final yards = amountRecord(modelName: 'player-prop-passing-yards');
      expect(bandRangeText(band('LOW', lo: 120, hi: 193), yards, yardsDisplay), '120–193 yds');
      expect(bandRangeText(band('LOW', lo: 0.2, hi: 0.6), amountRecord(modelName: 'player-prop-sacks'), modelDisplay('player-prop-sacks')), '0.2–0.6 sacks');
      expect(bandRangeText(band('HIGH', lo: 0.13), pickRecord(), pickDisplay), isNull);
    });

    test('counts use the right noun and singularize at one', () {
      expect(countText(9, 'games'), '9 games');
      expect(countText(1, 'games'), '1 game');
      expect(countText(402, 'golfers'), '402 golfers');
      expect(countText(1, 'players'), '1 player');
    });

    test('recent-period chips show a rounded percent for a pick and the miss for an amount', () {
      expect(periodValueText(pickRecord(), pickDisplay, 0.6249), '62%');
      expect(periodValueText(amountRecord(), marginDisplay, 11.94), '11.9');
    });
  });

  group('lean (missed high or low)', () {
    final record = amountRecord(marginOfError: 10.0);

    test('a positive bias means the model missed high, a negative one low', () {
      expect(leanText(4.2, record, marginDisplay), 'Missed high by 4.2 pts on average');
      expect(leanText(-3.1, record, marginDisplay), 'Missed low by 3.1 pts on average');
    });

    test('a lean under a tenth of the usual miss is no lean at all', () {
      expect(leanText(0.9, record, marginDisplay), 'No clear lean');
      expect(leanText(-0.9, record, marginDisplay), 'No clear lean');
    });

    test('uses the stat units and decimals', () {
      final yards = amountRecord(modelName: 'player-prop-passing-yards', marginOfError: 57.5);
      expect(leanText(12.34, yards, yardsDisplay), 'Missed high by 12.3 yds on average');
    });

    test('a pick model or a band with no data has nothing to say', () {
      expect(leanText(2.0, pickRecord(), pickDisplay), isNull);
      expect(leanText(null, record, marginDisplay), isNull);
    });

    test('the season-level fact reads High / Low / Neither', () {
      expect(seasonLeanText(amountRecord(bias: 2.4, marginOfError: 10.0), marginDisplay), 'High by 2.4 pts');
      expect(seasonLeanText(amountRecord(bias: -5.0, marginOfError: 10.0), marginDisplay), 'Low by 5.0 pts');
      expect(seasonLeanText(amountRecord(bias: 0.2, marginOfError: 10.0), marginDisplay), 'Neither');
      expect(seasonLeanText(pickRecord(), pickDisplay), isNull);
    });
  });

  group('chance models (PGA / F1 yes-no odds)', () {
    final record = chanceRecord();
    final display = modelDisplay('top-10-probability');

    test('grade like a pick: accuracy as a percent', () {
      expect(headlineLabel(record), 'ACCURACY');
      expect(headlineNumber(record, display, 0.812), '81.2%');
      expect(describeDelta(record, display)!.text, '+2.7 pts vs season');
      expect(atTrainingText(record, display), '80.4%');
      expect(periodValueText(record, display, 0.839), '84%');
    });

    test('are titled predicted chance, never confidence', () {
      expect(bandTitle(record), 'PREDICTED CHANCE');
    });

    test('the caption names what the chance is of and who it was for', () {
      expect(bandCaption(record, display), 'The top-10 chance we gave each golfer.');
      expect(bandCaption(chanceRecord(modelName: 'podium-probability', countNoun: 'drivers'), modelDisplay('podium-probability')), 'The podium chance we gave each driver.');
      expect(bandCaption(chanceRecord(modelName: 'dnf-probability', countNoun: 'drivers'), modelDisplay('dnf-probability')), 'The DNF chance we gave each driver.');
    });

    test('an F1 constructor card reads as a constructor win chance', () {
      final constructor = chanceRecord(modelName: 'constructor-win-probability', countNoun: 'constructors');

      expect(bandCaption(constructor, modelDisplay('constructor-win-probability')), 'The win chance we gave each constructor.');
      expect(modelDisplayName('constructor-win-probability'), 'Constructor Win Probability');
      expect(countText(10, nounFor(constructor, modelDisplay('constructor-win-probability'))), '10 constructors');
    });

    test('the bar line explains the 50% rule', () {
      expect(barCaption(record, display), 'Share we called right (yes if we gave over 50%, otherwise no).');
    });

    test('each band shows the chance range we stated', () {
      expect(bandRangeText(record.bands[0], record, display), '0–20%');
      expect(bandRangeText(record.bands[3], record, display), '60–80%');
    });

    test('there is no high-or-low lean for a yes/no model', () {
      expect(leanText(2.0, record, display), isNull);
    });

    test('the count noun comes from the sport when it sends one', () {
      expect(nounFor(record, display), 'golfers');
      expect(nounFor(chanceRecord(countNoun: 'drivers'), display), 'drivers');
      expect(nounFor(pickRecord(), modelDisplay('win-probability')), 'games');
    });
  });

  group('PGA / F1 units', () {
    test('a golfer score or round is in strokes; a finishing or grid slot is in places', () {
      expect(modelDisplay('projected-score-to-par').valueUnit, 'strokes');
      expect(modelDisplay('round-2').valueUnit, 'strokes');
      expect(modelDisplay('projected-finish-position').valueUnit, 'places');
      expect(modelDisplay('projected-qualifying-position').valueUnit, 'places');
      expect(modelDisplay('projected-sprint-grid-position').valueUnit, 'places');
    });

    test('display names read plainly', () {
      expect(modelDisplayName('projected-score-to-par'), 'Projected Score To Par');
      expect(modelDisplayName('round-3'), 'Round 3');
      expect(modelDisplayName('top-10-probability'), 'Top 10 Probability');
    });
  });

  group('model display registry', () {
    test('team-score models count teams; props count players; everything else games', () {
      expect(modelDisplay('home-score').countNoun, 'teams');
      expect(modelDisplay('away-score').countNoun, 'teams');
      expect(modelDisplay('player-prop-sacks').countNoun, 'players');
      expect(modelDisplay('score-margin').countNoun, 'games');
      expect(modelDisplay('win-probability').countNoun, 'games');
    });

    test('units come from the stat', () {
      expect(modelDisplay('player-prop-passing-yards').valueUnit, 'yds');
      expect(modelDisplay('player-prop-receiving-touchdowns').valueUnit, 'TDs');
      expect(modelDisplay('player-prop-defensive-sacks').valueUnit, 'sacks');
      expect(modelDisplay('player-prop-points').valueUnit, 'pts');
      expect(modelDisplay('home-score').valueUnit, 'pts');
    });

    test('display names title-case the model id', () {
      expect(modelDisplayName('win-probability'), 'Win Probability');
      expect(modelDisplayName('player-prop-passing-yards'), 'Player Prop Passing Yards');
    });
  });
}

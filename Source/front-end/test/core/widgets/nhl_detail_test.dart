import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/models/event.dart';
import 'package:front_end/core/models/event_leaders.dart';
import 'package:front_end/core/models/prediction.dart';
import 'package:front_end/core/models/sport_config.dart';
import 'package:front_end/core/widgets/matchup_hero.dart';
import 'package:front_end/core/widgets/stat_value.dart';
import 'package:front_end/core/widgets/td_dots.dart';
import 'package:front_end/core/widgets/team_leaders_panel.dart';

SportEvent _event({String status = 'scheduled', bool? overtime, bool? shootout, Map<String, EventGoalie> goalies = const {}}) => SportEvent(
      eventId: '401803584',
      eventDate: '2026-10-10',
      kickoffTime: '2026-10-10T23:00:00Z',
      status: status,
      week: null,
      round: null,
      participants: [
        Participant(
          entityId: '13', role: 'home', abbreviation: 'NYR',
          result: status == 'completed' ? const ParticipantResult(score: 4, won: true) : null,
        ),
        Participant(
          entityId: '1', role: 'away', abbreviation: 'BOS',
          result: status == 'completed' ? const ParticipantResult(score: 3, won: false) : null,
        ),
      ],
      predictionComparison: null,
      leadersComparison: null,
      wentToOvertime: overtime,
      decidedByShootout: shootout,
      goalies: goalies,
    );

EventPrediction _prediction({Map<String, EventGoalie> goalies = const {}}) => EventPrediction(
      homeWinProbability: 0.56,
      homeWinProbabilityModelVersion: 1,
      margin: 0.5,
      homeScore: 3.2,
      awayScore: 2.7,
      leaders: null,
      goalies: goalies,
    );

Future<void> _pump(WidgetTester tester, Widget child, {double width = 900}) async {
  tester.view.physicalSize = Size(width, 1400);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(home: Scaffold(body: SingleChildScrollView(child: child))));
}

void main() {
  group('NHL models', () {
    test('an event carries how it ended and its probable goalies', () {
      final event = SportEvent.fromJson({
        'event_id': '9', 'status': 'completed', 'participants': <dynamic>[],
        'went_to_overtime': true, 'decided_by_shootout': true,
        'goalies': {
          'home': {'entity_id': '3151297', 'name': 'Igor Shesterkin', 'status': 'Confirmed'},
          'away': {'entity_id': '77', 'name': null, 'status': 'Expected'},
        },
      });

      expect(event.finalLabel, 'FINAL/SO');
      expect(event.goalies['home']!.displayName, 'Igor Shesterkin');
      expect(event.goalies['home']!.tag, 'Confirmed');
      expect(event.goalies['away']!.displayName, '77');
    });

    test('final label covers regulation, overtime and other sports', () {
      expect(_event(overtime: false, shootout: false).finalLabel, 'FINAL');
      expect(_event(overtime: true, shootout: false).finalLabel, 'FINAL/OT');
      expect(_event().finalLabel, 'FINAL');
    });

    test('a prediction names the goalie each side was computed for', () {
      final prediction = EventPrediction.fromJson({
        'predictions': {
          'win_probability': {'home_win_probability': 0.56, 'model_version': 1},
          'margin': {'value': 0.5}, 'home_score': {'value': 3.2}, 'away_score': {'value': 2.7},
        },
        'goalies': {'home': {'entity_id': 'g1', 'name': 'Igor Shesterkin', 'source': 'predicted'}},
      });

      expect(prediction.goalies['home']!.tag, 'Predicted');
      expect(prediction.goalies.containsKey('away'), isFalse);
    });

    test('other sports parse with no goalies', () {
      final event = SportEvent.fromJson({'event_id': '9', 'participants': <dynamic>[]});

      expect(event.goalies, isEmpty);
      expect(event.wentToOvertime, isNull);
    });

    test('goals use the touchdown dots', () {
      expect(tdDotSlots('goals'), 1);
      expect(tdDotUnit('goals'), 'G');
      expect(tdDotUnit('rushing_touchdowns'), 'TD');
    });

    test('NHL is registered as a head-to-head sport', () {
      final nhl = sportById(SportIds.nhl);

      expect(nhl.displayName, 'NHL');
      expect(nhl.eventShape, EventShape.headToHead);
    });
  });

  group('NHL matchup hero', () {
    const goalies = {
      'home': EventGoalie(entityId: 'g1', name: 'I. Shesterkin', tag: 'Expected'),
      'away': EventGoalie(entityId: 'g2', name: 'J. Korpisalo', tag: 'Predicted'),
    };

    testWidgets('keeps the existing hero and adds the starting goalies underneath', (tester) async {
      await _pump(tester, MatchupHero(sport: 'nhl', event: _event(), prediction: _prediction(goalies: goalies)));

      // The existing hero, unchanged: whole-number scores labelled PTS, PICK, the duo.
      expect(find.text('3 PTS'), findsNWidgets(2));
      expect(find.text('PICK'), findsOneWidget);
      expect(find.text('PRED TOTAL'), findsOneWidget);
      expect(find.text('HOME MARGIN'), findsOneWidget);
      expect(find.text('NYR GOALIE'), findsOneWidget);
      expect(find.text('BOS GOALIE'), findsOneWidget);
      expect(find.text('I. Shesterkin'), findsOneWidget);
      expect(find.text('EXPECTED'), findsOneWidget);
      expect(find.text('PREDICTED'), findsOneWidget);
      // Away sits left of home, like the team columns above.
      expect(tester.getCenter(find.text('BOS GOALIE')).dx, lessThan(tester.getCenter(find.text('NYR GOALIE')).dx));
    });

    testWidgets('falls back to the event\'s probable goalies when the prediction names none', (tester) async {
      await _pump(tester, MatchupHero(sport: 'nhl', event: _event(goalies: goalies), prediction: _prediction()));

      expect(find.text('J. Korpisalo'), findsOneWidget);
    });

    testWidgets('shows no goalie row without goalies, as for every other sport', (tester) async {
      await _pump(tester, MatchupHero(sport: 'nba', event: _event(), prediction: _prediction()));

      expect(find.textContaining('GOALIE'), findsNothing);
    });

    testWidgets('the goalie row fits a phone-width card', (tester) async {
      await _pump(tester, MatchupHero(sport: 'nhl', event: _event(), prediction: _prediction(goalies: goalies)), width: 360);

      expect(tester.takeException(), isNull);
      expect(find.text('NYR GOALIE'), findsOneWidget);
    });

    testWidgets('a shootout result reads FINAL/SO on the existing result hero', (tester) async {
      await _pump(
        tester,
        MatchupResultHero(sport: 'nhl', event: _event(status: 'completed', overtime: true, shootout: true), comparison: null),
      );

      expect(find.text('FINAL/SO'), findsOneWidget);
      expect(find.text('4'), findsOneWidget);
    });

    testWidgets('a regulation result still reads FINAL', (tester) async {
      await _pump(tester, MatchupResultHero(sport: 'nba', event: _event(status: 'completed'), comparison: null));

      expect(find.text('FINAL'), findsOneWidget);
    });
  });

  group('NHL player leaders', () {
    EventLeaders leaders() => EventLeaders.fromJson({
          'home': {
            'scoring': [{'entity_id': 's1', 'name': 'A. Panarin', 'goals': 0.4, 'assists': 0.7}],
            'shooting': [{'entity_id': 's1', 'name': 'A. Panarin', 'shots_total': 3.6}],
            'physical': [{'entity_id': 's2', 'name': 'J. Trouba', 'hits': 3.2}],
            'goaltending': [{'entity_id': 'g1', 'name': 'I. Shesterkin', 'saves': 27.4}],
          },
          'away': {'scoring': <dynamic>[], 'shooting': <dynamic>[], 'physical': <dynamic>[], 'goaltending': <dynamic>[]},
        });

    testWidgets('shows the hockey categories with whole numbers and the goal pie', (tester) async {
      await _pump(tester, TeamLeadersPanel(sport: 'nhl', homeAbbr: 'NYR', awayAbbr: 'BOS', leaders: leaders()));

      expect(find.text('PLAYER LEADERS'), findsOneWidget);
      for (final title in ['SCORING', 'SHOOTING', 'PHYSICAL', 'GOALTENDING']) {
        expect(find.text(title), findsOneWidget);
      }
      expect(find.textContaining('0 G', findRichText: true), findsOneWidget);
      expect(find.textContaining('1 AST', findRichText: true), findsOneWidget);
      expect(find.textContaining('4 SOG', findRichText: true), findsOneWidget);
      expect(find.textContaining('3 HIT', findRichText: true), findsOneWidget);
      expect(find.textContaining('27 SV', findRichText: true), findsOneWidget);
      final dots = tester.widget<TdDots>(find.byType(TdDots));
      expect(dots.value, 0.4);
      expect(dots.unit, 'G');
    });

    testWidgets('fits a phone-width card', (tester) async {
      await _pump(tester, TeamLeadersPanel(sport: 'nhl', homeAbbr: 'NYR', awayAbbr: 'BOS', leaders: leaders()), width: 360);

      expect(tester.takeException(), isNull);
    });

    testWidgets('predicted vs actual uses the same rows as the other sports', (tester) async {
      final comparison = EventLeadersComparison.fromJson({
        'home': {
          'scoring': [{'entity_id': 's1', 'name': 'A. Panarin', 'predicted': {'goals': 0.4, 'assists': 0.7}, 'actual': {'goals': 1, 'assists': 1}}],
          'goaltending': [{'entity_id': 'g1', 'name': 'I. Shesterkin', 'predicted': {'saves': 27.4}, 'actual': {'saves': 31}}],
        },
        'away': <String, dynamic>{},
      });

      await _pump(tester, TeamLeadersComparisonPanel(sport: 'nhl', homeAbbr: 'NYR', awayAbbr: 'BOS', comparison: comparison));

      expect(find.text('PLAYER PROPS -- PREDICTED VS ACTUAL'), findsOneWidget);
      expect(find.textContaining('1 G', findRichText: true), findsOneWidget);
      expect(find.textContaining('31 SV', findRichText: true), findsOneWidget);
      expect(find.byType(TdDots), findsOneWidget);
    });
  });
}

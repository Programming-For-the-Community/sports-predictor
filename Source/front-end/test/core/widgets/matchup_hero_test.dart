import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/models/event.dart';
import 'package:front_end/core/models/live_score.dart';
import 'package:front_end/core/models/prediction.dart';
import 'package:front_end/core/theme/app_colors.dart';
import 'package:front_end/core/widgets/matchup_hero.dart';
import 'package:front_end/core/widgets/pick_labels.dart';
import 'package:front_end/core/widgets/win_probability_bar.dart';
import 'package:front_end/static/confidence_tiers.dart';

final _tier = ConfidenceTier.forProbability(0.62).label;

SportEvent _completedEvent() => SportEvent(
      eventId: '401547419',
      eventDate: '2026-09-14',
      kickoffTime: '2026-09-14T17:00:00Z',
      status: 'completed',
      week: 2,
      round: null,
      participants: const [
        Participant(entityId: '12', role: 'home', result: ParticipantResult(score: 31, won: true)),
        Participant(entityId: '13', role: 'away', result: ParticipantResult(score: 17, won: false)),
      ],
      predictionComparison: null,
      leadersComparison: null,
    );

SportEvent _scheduledEvent() => SportEvent(
      eventId: '401547417',
      eventDate: '2026-09-14',
      kickoffTime: '2026-09-14T17:00:00Z',
      status: 'scheduled',
      week: 2,
      round: null,
      participants: const [
        Participant(entityId: '12', role: 'home', result: null),
        Participant(entityId: '13', role: 'away', result: null),
      ],
      predictionComparison: null,
      leadersComparison: null,
    );

Finder _textStyled(String text, Color color) => find.byWidgetPredicate(
      (widget) => widget is Text && widget.data == text && widget.style?.color == color,
    );

const _prediction = EventPrediction(
  homeWinProbability: 0.62,
  homeWinProbabilityModelVersion: 9,
  margin: 4.5,
  homeScore: 27.3,
  awayScore: 22.8,
  leaders: null,
);

Future<void> _pump(WidgetTester tester, Widget hero, {double width = 700, double textScale = 1}) => tester.pumpWidget(MaterialApp(
      home: MediaQuery(
        data: MediaQueryData(textScaler: TextScaler.linear(textScale)),
        child: Scaffold(body: SingleChildScrollView(child: SizedBox(width: width, child: hero))),
      ),
    ));

void main() {
  group('MatchupHero', () {
    testWidgets('before a game each side\'s win chance is the big number, with the model score under it', (tester) async {
      await _pump(tester, MatchupHero(sport: 'nfl', event: _scheduledEvent(), prediction: _prediction));

      // KC is home and favoured.
      expect(_textStyled('62%', AppColors.cyan), findsOneWidget);
      expect(_textStyled('38%', AppColors.inkSub), findsOneWidget);
      expect(_textStyled('27', AppColors.cyan), findsOneWidget);
      expect(_textStyled('23', AppColors.cyan), findsOneWidget);
      expect(find.byType(WinProbabilityBar), findsOneWidget);
      expect(_textStyled('KC -4.5', AppColors.cyan), findsOneWidget);
      expect(_textStyled('50.1', AppColors.cyan), findsOneWidget);
      expect(find.text(_tier), findsOneWidget);
      expect(find.textContaining('SEP 14'), findsOneWidget);
      expect(find.text('LIVE'), findsNothing);
    });

    testWidgets('a live game puts the real score in the big slot and keeps the model and win chance under it', (tester) async {
      const liveState = LiveEventState(live: true, detail: 'Q2 04:12', homeScore: 14, awayScore: 7);
      await _pump(tester, MatchupHero(sport: 'nfl', event: _scheduledEvent(), prediction: _prediction, liveState: liveState));

      expect(_textStyled('14', AppColors.ink), findsOneWidget);
      expect(_textStyled('7', AppColors.inkSub), findsOneWidget);
      expect(_textStyled('27 · 62%', AppColors.cyan), findsOneWidget);
      expect(_textStyled('23 · 38%', AppColors.cyan), findsOneWidget);
      expect(find.text('LIVE'), findsOneWidget);
      expect(find.text('Q2 04:12'), findsOneWidget);
      // The pre-game pick stays put.
      expect(find.text('KC -4.5'), findsOneWidget);
      expect(find.text(_tier), findsOneWidget);
    });

    testWidgets('a game ESPN reports over shows the final score and FINAL before storage catches up', (tester) async {
      const liveState = LiveEventState(live: false, completed: true, detail: 'Final', homeScore: 31, awayScore: 17);
      await _pump(tester, MatchupHero(sport: 'nfl', event: _scheduledEvent(), prediction: _prediction, liveState: liveState));

      expect(_textStyled('31', AppColors.ink), findsOneWidget);
      expect(_textStyled('17', AppColors.inkSub), findsOneWidget);
      expect(find.text('FINAL'), findsOneWidget);
      expect(find.text('LIVE'), findsNothing);
    });

    for (final (width, textScale) in [(360.0, 1.0), (320.0, 1.3)]) {
      testWidgets('a live card with a long status fits at $width px, text x$textScale', (tester) async {
        // ESPN's detail isn't always a short clock.
        const liveState = LiveEventState(live: true, detail: '2nd & Goal at NCAAFB 4 -- Timeout', homeScore: 114, awayScore: 107);
        await _pump(
          tester, MatchupHero(sport: 'ncaafb', event: _scheduledEvent(), prediction: _prediction, liveState: liveState),
          width: width, textScale: textScale,
        );

        expect(tester.takeException(), isNull);
        // Pills keep their words on a phone.
        expect(find.text('LIVE'), findsOneWidget);
        expect(find.text(_tier), findsOneWidget);
      });
    }
  });

  group('MatchupResultHero', () {
    const comparison = PredictionComparison(
      predictedHomeWinProbability: 0.67,
      predictedHomeWon: true,
      actualHomeWon: true,
      correct: true,
      predictedMargin: 10,
      actualMargin: 14,
      predictedHomeScore: 27.4,
      predictedAwayScore: 17.1,
      actualHomeScore: 31,
      actualAwayScore: 17,
    );

    testWidgets('shows the verdict in the pick\'s own terms, then the final line and total over the model\'s', (tester) async {
      await _pump(tester, MatchupResultHero(sport: 'nfl', event: _completedEvent(), comparison: comparison));

      expect(_textStyled('31', AppColors.ink), findsOneWidget);
      expect(_textStyled('17', AppColors.inkSub), findsOneWidget);
      expect(_textStyled('27', AppColors.cyan), findsOneWidget);
      expect(find.text(VerdictPill.hitLabel), findsOneWidget);
      expect(_textStyled('KC 67%', AppColors.cyan), findsOneWidget);
      expect(_textStyled('KC -14', AppColors.ink), findsOneWidget);
      expect(_textStyled('KC -10.0', AppColors.cyan), findsOneWidget);
      expect(_textStyled('48', AppColors.ink), findsOneWidget);
      expect(_textStyled('44.5', AppColors.cyan), findsOneWidget);
      expect(find.textContaining('HOME'), findsNothing);
    });

    testWidgets('a wrong away pick reads MISS with the away side\'s chance', (tester) async {
      const missed = PredictionComparison(
        predictedHomeWinProbability: 0.36, predictedHomeWon: false, actualHomeWon: true, correct: false,
        predictedMargin: -3.5, actualMargin: 14, predictedHomeScore: 20, predictedAwayScore: 23.5,
        actualHomeScore: 31, actualAwayScore: 17,
      );
      await _pump(tester, MatchupResultHero(sport: 'nfl', event: _completedEvent(), comparison: missed));

      expect(find.text(VerdictPill.missLabel), findsOneWidget);
      expect(find.text('LV 64%'), findsOneWidget);
      expect(_textStyled('LV -3.5', AppColors.cyan), findsOneWidget);
    });

    testWidgets('a pick logged without a margin or scores shows only the final figures', (tester) async {
      const bare = PredictionComparison(
        predictedHomeWinProbability: 0.67, predictedHomeWon: true, actualHomeWon: true, correct: true,
        predictedMargin: null, actualMargin: 14, predictedHomeScore: null, predictedAwayScore: null,
        actualHomeScore: 31, actualAwayScore: 17,
      );
      await _pump(tester, MatchupResultHero(sport: 'nfl', event: _completedEvent(), comparison: bare), width: 320, textScale: 1.3);

      expect(tester.takeException(), isNull);
      expect(_textStyled('KC -14', AppColors.ink), findsOneWidget);
      expect(find.textContaining('-10'), findsNothing);
    });

    testWidgets('says so when no prediction was recorded', (tester) async {
      await _pump(tester, MatchupResultHero(sport: 'nfl', event: _completedEvent(), comparison: null));

      expect(find.text(MatchupResultHero.noPredictionLabel), findsOneWidget);
      expect(find.byType(VerdictPill), findsNothing);
    });
  });
}

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/models/event.dart';
import 'package:front_end/core/models/live_score.dart';
import 'package:front_end/core/models/prediction.dart';
import 'package:front_end/core/theme/app_colors.dart';
import 'package:front_end/core/widgets/matchup_hero.dart';

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

// Locates a Text widget rendering exactly `text` -- used below for the
// smaller companion predicted-score numeral, which (unlike the big live
// numeral) isn't wrapped in a ShaderMask so its own style is checkable
// directly.
Finder _textStyled(String text, Color color) => find.byWidgetPredicate(
      (widget) => widget is Text && widget.data == text && widget.style?.color == color,
    );

void main() {
  group('MatchupHero live scoring', () {
    const prediction = EventPrediction(
      homeWinProbability: 0.62,
      homeWinProbabilityModelVersion: 9,
      margin: 4.5,
      homeScore: 27.3,
      awayScore: 22.8,
      leaders: null,
    );

    testWidgets('a live event shows the real score alongside the still-visible predicted one, in cyan', (tester) async {
      const liveState = LiveEventState(live: true, detail: 'Q2 04:12', homeScore: 14, awayScore: 7);

      await tester.pumpWidget(MaterialApp(
        home: Scaffold(body: MatchupHero(sport: 'nfl', event: _scheduledEvent(), prediction: prediction, liveState: liveState)),
      ));

      // Live scores render as the big numeral.
      expect(find.textContaining('14 PTS'), findsOneWidget);
      expect(find.textContaining('7 PTS'), findsOneWidget);
      // The pre-game predicted scores stay visible too, smaller and
      // cyan, not swapped out by the live ones.
      expect(_textStyled('27', AppColors.cyan), findsOneWidget);
      expect(_textStyled('23', AppColors.cyan), findsOneWidget);
      // Confidence and the spread stay visible alongside the LIVE
      // banner/clock, not replaced by it. homeWinProbability 0.62 -> edge
      // 0.12 -> MED tier.
      expect(find.text('Q2 04:12'), findsOneWidget);
      expect(find.text('MED'), findsOneWidget);
      expect(find.text('HOME MARGIN'), findsOneWidget);
    });

    testWidgets('the confidence pill sits with PICK, below the LIVE banner/clock -- not next to it', (tester) async {
      const liveState = LiveEventState(live: true, detail: 'Q2 04:12', homeScore: 14, awayScore: 7);

      await tester.pumpWidget(MaterialApp(
        home: Scaffold(body: MatchupHero(sport: 'nfl', event: _scheduledEvent(), prediction: prediction, liveState: liveState)),
      ));

      final liveY = tester.getCenter(find.text('Q2 04:12')).dy;
      final pickLabelY = tester.getCenter(find.text('PICK')).dy;
      final confidenceY = tester.getCenter(find.text('MED')).dy;

      expect(liveY, lessThan(pickLabelY));
      expect(confidenceY, greaterThan(pickLabelY));
    });

    testWidgets('a live event on a narrow (mobile) width collapses the LIVE/confidence pills to just their dots',
        (tester) async {
      const liveState = LiveEventState(live: true, detail: 'Q2 04:12', homeScore: 14, awayScore: 7);

      await tester.pumpWidget(MaterialApp(
        home: Scaffold(
          body: SizedBox(
            width: 560,
            child: MatchupHero(sport: 'nfl', event: _scheduledEvent(), prediction: prediction, liveState: liveState),
          ),
        ),
      ));

      expect(find.text('LIVE'), findsNothing);
      expect(find.text('MED'), findsNothing);
      expect(find.byTooltip('LIVE'), findsOneWidget);
      expect(find.byTooltip('MED'), findsOneWidget);
      // The rest of the card (game clock, spread) is unaffected.
      expect(find.text('Q2 04:12'), findsOneWidget);
      expect(find.text('HOME MARGIN'), findsOneWidget);
    });

    testWidgets('a long live-detail string does not overflow a phone-width card', (tester) async {
      // ESPN's own detail text isn't always a short clock -- situational
      // strings ("End of 2nd Quarter", a weather delay message) run long
      // enough to overflow the LIVE row if it isn't allowed to shrink.
      const liveState = LiveEventState(live: true, detail: '2nd & Goal at NCAAFB 4 -- Timeout', homeScore: 14, awayScore: 7);

      await tester.pumpWidget(MaterialApp(
        home: Scaffold(
          body: SizedBox(
            width: 360,
            child: MatchupHero(sport: 'ncaafb', event: _scheduledEvent(), prediction: prediction, liveState: liveState),
          ),
        ),
      ));

      expect(tester.takeException(), isNull);
    });

    testWidgets('a pre-game (not yet live) event shows only the predicted score, in cyan', (tester) async {
      await tester.pumpWidget(MaterialApp(
        home: Scaffold(body: MatchupHero(sport: 'nfl', event: _scheduledEvent(), prediction: prediction)),
      ));

      expect(find.textContaining('27 PTS'), findsOneWidget);
      expect(find.textContaining('23 PTS'), findsOneWidget);
      // No live numeral, no smaller companion value either.
      expect(_textStyled('27', AppColors.cyan), findsNothing);
    });
  });
  testWidgets('MatchupResultHero shows -- for a missing predicted margin instead of dropping the trio', (tester) async {
    const comparison = PredictionComparison(
      predictedHomeWinProbability: 0.67,
      predictedHomeWon: true,
      actualHomeWon: true,
      correct: true,
      predictedMargin: null,
      actualMargin: 14,
      predictedHomeScore: null,
      predictedAwayScore: null,
      actualHomeScore: 31,
      actualAwayScore: 17,
    );

    await tester.pumpWidget(MaterialApp(
      home: Scaffold(body: MatchupResultHero(sport: 'nfl', event: _completedEvent(), comparison: comparison)),
    ));

    expect(find.text('PRED MARGIN'), findsOneWidget);
    expect(find.text('--'), findsOneWidget);
  });
}

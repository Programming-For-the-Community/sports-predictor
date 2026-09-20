import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/models/field_live_score.dart';
import 'package:front_end/core/models/field_prediction.dart';
import 'package:front_end/features/events/two_sided_pga_matchup.dart';

const _usa = MatchPlaySide(entityId: '1', name: 'USA');
const _intl = MatchPlaySide(entityId: '2', name: 'INTL');

TwoSidedPgaPrediction _prediction({
  String? tournamentName = 'Presidents Cup',
  String? sessionName = 'Thursday Foursomes',
  String? status,
  Object? home = _usa, // MatchPlaySide, or null to test the no-side fallback
  Object? away = _intl,
  ModelValue? winProbability,
  bool? actualHomeWon,
  bool? actualHalved,
}) =>
    TwoSidedPgaPrediction(
      eventId: '1',
      eventType: 'cup',
      tournamentName: tournamentName,
      sessionName: sessionName,
      status: status,
      home: home as MatchPlaySide?,
      away: away as MatchPlaySide?,
      winProbability: winProbability,
      actualHomeWon: actualHomeWon,
      actualHalved: actualHalved,
    );

Widget _wrap(TwoSidedPgaPrediction prediction, {TwoSidedLiveEventState? liveState}) =>
    MaterialApp(home: Scaffold(body: TwoSidedPgaMatchup(prediction: prediction, liveState: liveState)));

void main() {
  group('TwoSidedPgaMatchup', () {
    testWidgets('renders the tournament and session names', (tester) async {
      await tester.pumpWidget(_wrap(_prediction()));

      expect(find.text('Presidents Cup'), findsOneWidget);
      expect(find.text('Thursday Foursomes'), findsOneWidget);
    });

    testWidgets('renders each side\'s golfers joined by a slash', (tester) async {
      await tester.pumpWidget(_wrap(_prediction(
        home: const MatchPlaySide(entityId: '1', name: 'USA', golfers: [
          MatchPlaySideGolfer(entityId: 'a', name: 'Tony Finau'),
          MatchPlaySideGolfer(entityId: 'b', name: 'Max Homa'),
        ]),
      )));

      expect(find.text('Tony Finau / Max Homa'), findsOneWidget);
    });

    testWidgets('shows the win probability split when a model is promoted', (tester) async {
      await tester.pumpWidget(_wrap(_prediction(winProbability: const ModelValue(value: 0.65, modelVersion: 1))));

      expect(find.textContaining('USA 65%'), findsOneWidget);
      expect(find.textContaining('INTL 35%'), findsOneWidget);
    });

    testWidgets('falls back to a no-prediction message when no model is promoted', (tester) async {
      await tester.pumpWidget(_wrap(_prediction(winProbability: null)));

      expect(find.text('No prediction available yet.'), findsOneWidget);
    });

    testWidgets('sides default to Home/Away when null', (tester) async {
      await tester.pumpWidget(_wrap(_prediction(
        home: null,
        away: null,
        winProbability: const ModelValue(value: 0.5, modelVersion: 1),
      )));

      expect(find.text('Home'), findsOneWidget);
      expect(find.text('Away'), findsOneWidget);
      expect(find.textContaining('Home 50%'), findsOneWidget);
      expect(find.textContaining('Away 50%'), findsOneWidget);
    });

    testWidgets('shows a live badge while the liveState is not completed', (tester) async {
      await tester.pumpWidget(_wrap(
        _prediction(),
        liveState: const TwoSidedLiveEventState(eventType: 'cup', status: 'in_progress'),
      ));

      expect(find.text('LIVE'), findsOneWidget);
    });

    testWidgets('hides the live badge once liveState reports completed', (tester) async {
      await tester.pumpWidget(_wrap(
        _prediction(),
        liveState: const TwoSidedLiveEventState(eventType: 'cup', status: 'completed'),
      ));

      expect(find.text('LIVE'), findsNothing);
    });

    testWidgets('hides the live badge when no liveState is given at all', (tester) async {
      await tester.pumpWidget(_wrap(_prediction()));

      expect(find.text('LIVE'), findsNothing);
    });

    testWidgets('prefers margin_display over points for a live match side', (tester) async {
      await tester.pumpWidget(_wrap(
        _prediction(),
        liveState: const TwoSidedLiveEventState(eventType: 'cup', status: 'in_progress', participants: {
          '1': TwoSidedParticipantLiveResult(marginDisplay: '6 & 5', points: 3.0),
        }),
      ));

      expect(find.text('6 & 5'), findsOneWidget);
      expect(find.text('3.0 pts'), findsNothing);
    });

    testWidgets('shows points when no margin_display is present', (tester) async {
      await tester.pumpWidget(_wrap(
        _prediction(),
        liveState: const TwoSidedLiveEventState(eventType: 'cup', status: 'in_progress', participants: {
          '1': TwoSidedParticipantLiveResult(points: 2.5),
        }),
      ));

      expect(find.text('2.5 pts'), findsOneWidget);
    });

    testWidgets('shows the winning side once the event is completed', (tester) async {
      await tester.pumpWidget(_wrap(_prediction(status: 'completed', actualHomeWon: true, actualHalved: false)));

      expect(find.text('USA won'), findsOneWidget);
    });

    testWidgets('shows "Match halved" when the result was a tie', (tester) async {
      await tester.pumpWidget(_wrap(_prediction(status: 'completed', actualHomeWon: false, actualHalved: true)));

      expect(find.text('Match halved'), findsOneWidget);
    });

    testWidgets('shows no actual-result line before the event is completed', (tester) async {
      await tester.pumpWidget(_wrap(_prediction(status: 'scheduled', actualHomeWon: null)));

      expect(find.text('USA won'), findsNothing);
      expect(find.text('INTL won'), findsNothing);
      expect(find.text('Match halved'), findsNothing);
    });
  });
}

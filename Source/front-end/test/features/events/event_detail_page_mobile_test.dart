import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/data/events_repository.dart';
import 'package:front_end/core/data/live_scores_repository.dart';
import 'package:front_end/core/models/event.dart';
import 'package:front_end/core/models/live_score.dart';
import 'package:front_end/core/models/prediction.dart';
import 'package:front_end/features/events/event_detail_page.dart';

import '../../support/mobile_viewport.dart';

final _event = SportEvent(
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

const _prediction = EventPrediction(
  homeWinProbability: 0.62,
  homeWinProbabilityModelVersion: 9,
  margin: 4.5,
  homeScore: 27.3,
  awayScore: 22.8,
  leaders: null,
);

/// MatchupHero (core/widgets/matchup_hero.dart) is what UI #6's large-
/// PRED-TOTAL/small-confidence restyle touched -- worth its own mobile
/// check rather than relying only on the other pages' coverage.
void main() {
  for (final width in mobileViewportWidths) {
    testWidgets('renders with no overflow at ${width}px wide', (tester) async {
      await pumpAtWidth(
        tester,
        width,
        ProviderScope(
          overrides: [
            eventsListProvider.overrideWith((ref, query) async => query.status == 'scheduled' ? [_event] : []),
            eventPredictionProvider.overrideWith((ref, query) async => _prediction),
            liveScoresProvider.overrideWith((ref, sport) async => const {}),
          ],
          child: const MaterialApp(home: Scaffold(body: EventDetailPage(sportId: 'nfl', eventId: '401547417'))),
        ),
      );

      expect(tester.takeException(), isNull);
    });

    testWidgets('renders with no overflow while live at ${width}px wide', (tester) async {
      await pumpAtWidth(
        tester,
        width,
        ProviderScope(
          overrides: [
            eventsListProvider.overrideWith((ref, query) async => query.status == 'scheduled' ? [_event] : []),
            eventPredictionProvider.overrideWith((ref, query) async => _prediction),
            liveScoresProvider.overrideWith(
              (ref, sport) async => {
                '401547417': const LiveEventState(live: true, detail: 'Q3 08:14', homeScore: 17, awayScore: 14),
              },
            ),
          ],
          child: const MaterialApp(home: Scaffold(body: EventDetailPage(sportId: 'nfl', eventId: '401547417'))),
        ),
      );

      expect(tester.takeException(), isNull);
    });
  }
}

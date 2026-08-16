import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/data/events_repository.dart';
import 'package:front_end/core/data/live_scores_repository.dart';
import 'package:front_end/core/models/event.dart';
import 'package:front_end/core/models/event_leaders.dart';
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

// Real-length names + multiple candidates per category -- the previous
// leaders/leadersComparison: null fixtures never exercised
// TeamLeadersPanel/TeamLeadersComparisonPanel's real layout at all (see
// team_leaders_panel.dart's own side-by-side-columns overflow fix).
const _predictionWithLeaders = EventPrediction(
  homeWinProbability: 0.62,
  homeWinProbabilityModelVersion: 9,
  margin: 4.5,
  homeScore: 27.3,
  awayScore: 22.8,
  leaders: EventLeaders(
    home: TeamLeaders({
      'passing': [PlayerStatLine(entityId: '1', name: 'Christian McCaffrey Jr.', stats: {'passing_yards': 312, 'passing_touchdowns': 3})],
      'receiving': [
        PlayerStatLine(entityId: '2', name: 'DeAndre Hopkins-Wilson', stats: {'receiving_yards': 118, 'receiving_touchdowns': 1}),
        PlayerStatLine(entityId: '3', name: 'Christopher Godwin', stats: {'receiving_yards': 74, 'receiving_touchdowns': 0}),
      ],
      'rushing': [PlayerStatLine(entityId: '4', name: 'Jonathan Taylor', stats: {'rushing_yards': 96, 'rushing_touchdowns': 1})],
      'sacks': [PlayerStatLine(entityId: '5', name: 'Myles Garrett', stats: {'defensive_sacks': 2.5})],
    }),
    away: TeamLeaders({
      'passing': [PlayerStatLine(entityId: '6', name: 'Justin Herbert', stats: {'passing_yards': 289, 'passing_touchdowns': 2})],
      'receiving': [PlayerStatLine(entityId: '7', name: 'Amon-Ra St. Brown', stats: {'receiving_yards': 101, 'receiving_touchdowns': 1})],
      'rushing': [PlayerStatLine(entityId: '8', name: 'Derrick Henry', stats: {'rushing_yards': 88, 'rushing_touchdowns': 1})],
      'sacks': [PlayerStatLine(entityId: '9', name: 'Nick Bosa', stats: {'defensive_sacks': 1.5})],
    }),
  ),
);

final _completedEvent = SportEvent(
  eventId: '401547417',
  eventDate: '2026-09-14',
  kickoffTime: '2026-09-14T17:00:00Z',
  status: 'completed',
  week: 2,
  round: null,
  participants: const [
    Participant(entityId: '12', role: 'home', result: ParticipantResult(score: 27, won: true)),
    Participant(entityId: '13', role: 'away', result: ParticipantResult(score: 23, won: false)),
  ],
  predictionComparison: const PredictionComparison(
    predictedHomeWinProbability: 0.62,
    predictedHomeWon: true,
    actualHomeWon: true,
    correct: true,
    predictedMargin: 4.5,
    actualMargin: 4,
    predictedHomeScore: 27.3,
    predictedAwayScore: 22.8,
    actualHomeScore: 27,
    actualAwayScore: 23,
  ),
  leadersComparison: const EventLeadersComparison(
    home: TeamLeadersComparison({
      'passing': [
        PlayerStatLineComparison(
          entityId: '1', name: 'Christian McCaffrey Jr.',
          predicted: {'passing_yards': 312}, actual: {'passing_yards': 298},
        ),
      ],
      'receiving': [
        PlayerStatLineComparison(
          entityId: '2', name: 'DeAndre Hopkins-Wilson',
          predicted: {'receiving_yards': 118}, actual: {'receiving_yards': 132},
        ),
      ],
      'rushing': [
        PlayerStatLineComparison(
          entityId: '4', name: 'Jonathan Taylor',
          predicted: {'rushing_yards': 96}, actual: {'rushing_yards': 71},
        ),
      ],
      'sacks': [],
    }),
    away: TeamLeadersComparison({
      'passing': [
        PlayerStatLineComparison(
          entityId: '6', name: 'Justin Herbert',
          predicted: {'passing_yards': 289}, actual: {'passing_yards': 301},
        ),
      ],
      'receiving': [],
      'rushing': [],
      'sacks': [
        PlayerStatLineComparison(entityId: '9', name: 'Nick Bosa', predicted: {'defensive_sacks': 1.5}, actual: {'defensive_sacks': 2}),
      ],
    }),
  ),
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

    testWidgets('renders player leaders with no overflow at ${width}px wide', (tester) async {
      await pumpAtWidth(
        tester,
        width,
        ProviderScope(
          overrides: [
            eventsListProvider.overrideWith((ref, query) async => query.status == 'scheduled' ? [_event] : []),
            eventPredictionProvider.overrideWith((ref, query) async => _predictionWithLeaders),
            liveScoresProvider.overrideWith((ref, sport) async => const {}),
          ],
          child: const MaterialApp(home: Scaffold(body: EventDetailPage(sportId: 'nfl', eventId: '401547417'))),
        ),
      );

      expect(tester.takeException(), isNull);
    });

    testWidgets('renders completed player-props comparison with no overflow at ${width}px wide', (tester) async {
      await pumpAtWidth(
        tester,
        width,
        ProviderScope(
          overrides: [
            eventsListProvider.overrideWith((ref, query) async => query.status == 'completed' ? [_completedEvent] : []),
          ],
          child: const MaterialApp(home: Scaffold(body: EventDetailPage(sportId: 'nfl', eventId: '401547417'))),
        ),
      );

      expect(tester.takeException(), isNull);
    });
  }
}

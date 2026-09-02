import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/data/field_events_repository.dart';
import 'package:front_end/core/data/live_scores_repository.dart';
import 'package:front_end/core/models/field_live_score.dart';
import 'package:front_end/core/models/field_prediction.dart';
import 'package:front_end/features/events/field_event_detail_page.dart';

import '../../support/mobile_viewport.dart';

Map<String, dynamic> _fieldResponse() => {
      'sport': 'pga', 'event_id': '401811963', 'event_type': 'field',
      'tournament_name': 'BMW Championship at Caves Valley Golf Club', 'status': 'scheduled', 'par': 70,
      'cutline': {
        'projected_cut_score': {'value': -2.0, 'model_version': 3},
      },
      'field': [
        {
          'entity_id': '10140', 'name': 'Cristóbal Del Solar-Hernández', 'country': 'CHI',
          'predictions': {
            'top_10_probability': {'value': 0.42, 'model_version': 1},
            'top_5_probability': {'value': 0.21, 'model_version': 1},
            'projected_score_to_par': {'value': -8.5, 'model_version': 2},
          },
        },
      ],
    };

// A team side with golfer names -- Ryder/Presidents Cup foursomes, the
// widest _SideLine content two_sided_pga_matchup.dart ever renders.
Map<String, dynamic> _cupResponse() => {
      'sport': 'pga', 'event_id': '401465497-match-10951', 'event_type': 'match_play',
      'tournament_name': 'Presidents Cup at Royal Montreal Golf Club', 'status': 'scheduled',
      'match_format': 'Foursomes', 'session_name': 'Session 1 -- Friday Morning Foursomes',
      'home': {
        'entity_id': '1', 'name': 'United States',
        'golfers': [
          {'entity_id': '1a', 'name': 'Scottie Scheffler'},
          {'entity_id': '1b', 'name': 'Xander Schauffele'},
        ],
      },
      'away': {
        'entity_id': '3', 'name': 'International Team',
        'golfers': [
          {'entity_id': '3a', 'name': 'Hideki Matsuyama'},
          {'entity_id': '3b', 'name': 'Sungjae Im'},
        ],
      },
      'predictions': {
        'match_win_probability': {'value': 0.58, 'model_version': 1},
      },
    };

Map<String, dynamic> _completedCupResponse() => {
      ..._cupResponse(),
      'status': 'completed',
      'actual': {'home_won': true, 'halved': false},
    };

/// Dedicated mobile check for FieldEventDetailPage's two divergent
/// prediction shapes -- FieldLeaderboardTable's own compact-column
/// behavior already has its own coverage in field_leaderboard_table_test.
/// dart, so this file's own field-event case just checks the page shell
/// around it. TwoSidedPgaMatchup (the match_play/cup branch) has no
/// coverage anywhere else at all -- this is its only mobile check.
void main() {
  for (final width in mobileViewportWidths) {
    testWidgets('a field event renders with no overflow at ${width}px wide', (tester) async {
      await pumpAtWidth(
        tester,
        width,
        ProviderScope(
          overrides: [
            fieldEventPredictionProvider.overrideWith((ref, query) async => parsePgaEventPrediction(_fieldResponse())),
            fieldLiveScoresProvider.overrideWith((ref, sport) async => const <String, FieldLiveEventState>{}),
          ],
          child: const MaterialApp(home: Scaffold(body: FieldEventDetailPage(sportId: 'pga', eventId: '401811963'))),
        ),
      );

      expect(tester.takeException(), isNull);
    });

    testWidgets('a live field event renders with no overflow at ${width}px wide', (tester) async {
      await pumpAtWidth(
        tester,
        width,
        ProviderScope(
          overrides: [
            fieldEventPredictionProvider.overrideWith((ref, query) async => parsePgaEventPrediction(_fieldResponse())),
            fieldLiveScoresProvider.overrideWith(
              (ref, sport) async => {
                '401811963': const FieldLiveEventState(
                  status: 'scheduled', tournamentName: 'BMW Championship at Caves Valley Golf Club',
                  participants: {
                    '10140': FieldParticipantLiveResult(finishPosition: 3, status: 'in_progress', scoreToPar: -6, thru: 14),
                  },
                ),
              },
            ),
          ],
          child: const MaterialApp(home: Scaffold(body: FieldEventDetailPage(sportId: 'pga', eventId: '401811963'))),
        ),
      );

      expect(tester.takeException(), isNull);
    });

    testWidgets('a scheduled team match_play (cup) event renders with no overflow at ${width}px wide', (tester) async {
      await pumpAtWidth(
        tester,
        width,
        ProviderScope(
          overrides: [
            fieldEventPredictionProvider.overrideWith((ref, query) async => parsePgaEventPrediction(_cupResponse())),
            fieldLiveScoresProvider.overrideWith((ref, sport) async => const <String, FieldLiveEventState>{}),
            pgaLiveScoresProvider.overrideWith((ref, sport) async => const <String, PgaLiveEventState>{}),
          ],
          child: const MaterialApp(home: Scaffold(body: FieldEventDetailPage(sportId: 'pga', eventId: '401465497-match-10951'))),
        ),
      );

      expect(tester.takeException(), isNull);
    });

    testWidgets('a live team match_play (cup) event renders with no overflow at ${width}px wide', (tester) async {
      await pumpAtWidth(
        tester,
        width,
        ProviderScope(
          overrides: [
            fieldEventPredictionProvider.overrideWith((ref, query) async => parsePgaEventPrediction(_cupResponse())),
            fieldLiveScoresProvider.overrideWith((ref, sport) async => const <String, FieldLiveEventState>{}),
            pgaLiveScoresProvider.overrideWith(
              (ref, sport) async => {
                '401465497-match-10951': parsePgaLiveEventState({
                  'event_type': 'match_play', 'status': 'scheduled', 'tournament_name': 'Presidents Cup at Royal Montreal Golf Club',
                  'participants': {
                    '1': {'status': 'in_progress', 'margin_display': '2 up', 'margin_holes': 2.0},
                    '3': {'status': 'in_progress'},
                  },
                }),
              },
            ),
          ],
          child: const MaterialApp(home: Scaffold(body: FieldEventDetailPage(sportId: 'pga', eventId: '401465497-match-10951'))),
        ),
      );

      expect(tester.takeException(), isNull);
    });

    testWidgets('a completed team match_play (cup) event renders its actual-result line with no overflow at ${width}px wide', (tester) async {
      await pumpAtWidth(
        tester,
        width,
        ProviderScope(
          overrides: [
            fieldEventPredictionProvider.overrideWith((ref, query) async => parsePgaEventPrediction(_completedCupResponse())),
            fieldLiveScoresProvider.overrideWith((ref, sport) async => const <String, FieldLiveEventState>{}),
            pgaLiveScoresProvider.overrideWith((ref, sport) async => const <String, PgaLiveEventState>{}),
          ],
          child: const MaterialApp(home: Scaffold(body: FieldEventDetailPage(sportId: 'pga', eventId: '401465497-match-10951'))),
        ),
      );

      expect(tester.takeException(), isNull);
    });
  }
}

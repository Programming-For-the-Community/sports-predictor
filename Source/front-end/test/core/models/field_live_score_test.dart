import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/models/field_live_score.dart';

void main() {
  test('parses a tournament-level state with per-golfer results keyed by entity_id', () {
    final state = FieldLiveEventState.fromJson({
      'status': 'scheduled',
      'tournament_name': 'BMW Championship',
      'participants': {
        '10140': {'finish_position': 26, 'is_tie': true, 'status': 'finished', 'score_to_par': -4, 'total_strokes': 276.0},
      },
    });

    expect(state.status, 'scheduled');
    expect(state.tournamentName, 'BMW Championship');
    expect(state.participants['10140']!.finishPosition, 26);
    expect(state.participants['10140']!.status, 'finished');
  });

  test('defaults to an empty participants map when absent', () {
    final state = FieldLiveEventState.fromJson({'status': 'scheduled'});

    expect(state.participants, isEmpty);
  });

  test('parses per-round live results keyed by round number', () {
    final state = FieldLiveEventState.fromJson({
      'status': 'scheduled',
      'participants': {
        '10140': {
          'status': 'scheduled',
          'rounds': [
            {'round': 1, 'score_to_par': -4, 'total_strokes': 68.0},
          ],
        },
      },
    });

    final rounds = state.participants['10140']!.rounds;
    expect(rounds[1]!.scoreToPar, -4);
    expect(rounds[1]!.totalStrokes, 68.0);
  });

  test('rounds defaults to empty when absent', () {
    final state = FieldLiveEventState.fromJson({
      'status': 'scheduled',
      'participants': {
        '10140': {'status': 'scheduled'},
      },
    });

    expect(state.participants['10140']!.rounds, isEmpty);
  });

  group('TwoSidedLiveEventState', () {
    test('parses a match_play entry with won/margin fields', () {
      final state = TwoSidedLiveEventState.fromJson({
        'event_type': 'match_play',
        'status': 'scheduled',
        'tournament_name': 'Presidents Cup',
        'participants': {
          '1': {'status': 'finished', 'won': true, 'halved': false, 'margin_display': '6 & 5', 'margin_holes': 6.0},
          '3': {'status': 'finished', 'won': false, 'halved': false},
        },
      });

      expect(state.eventType, 'match_play');
      expect(state.participants['1']!.won, isTrue);
      expect(state.participants['1']!.marginDisplay, '6 & 5');
      expect(state.participants['1']!.points, isNull);
    });

    test('parses a cup entry with points instead of a margin', () {
      final state = TwoSidedLiveEventState.fromJson({
        'event_type': 'cup',
        'status': 'scheduled',
        'participants': {
          '1': {'points': 17.5, 'won': true, 'halved': false},
        },
      });

      expect(state.participants['1']!.points, 17.5);
      expect(state.participants['1']!.marginDisplay, isNull);
    });
  });

  group('parsePgaLiveEventState dispatch', () {
    test('field event_type parses into a PgaFieldLiveState', () {
      final result = parsePgaLiveEventState({'event_type': 'field', 'status': 'scheduled', 'participants': <String, dynamic>{}});

      expect(result, isA<PgaFieldLiveState>());
    });

    test('missing event_type defaults to field (pre-this-pass cache compatibility)', () {
      final result = parsePgaLiveEventState({'status': 'scheduled', 'participants': <String, dynamic>{}});

      expect(result, isA<PgaFieldLiveState>());
    });

    test('match_play event_type parses into a PgaTwoSidedLiveState', () {
      final result = parsePgaLiveEventState({'event_type': 'match_play', 'status': 'scheduled', 'participants': <String, dynamic>{}});

      expect(result, isA<PgaTwoSidedLiveState>());
    });

    test('cup event_type parses into a PgaTwoSidedLiveState', () {
      final result = parsePgaLiveEventState({'event_type': 'cup', 'status': 'scheduled', 'participants': <String, dynamic>{}});

      expect(result, isA<PgaTwoSidedLiveState>());
    });

    test('an unrecognized event_type throws FormatException', () {
      expect(
        () => parsePgaLiveEventState({'event_type': 'nonsense', 'status': 'scheduled', 'participants': <String, dynamic>{}}),
        throwsFormatException,
      );
    });
  });
}

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
}

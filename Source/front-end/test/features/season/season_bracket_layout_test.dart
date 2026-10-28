import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/models/season_projection.dart';
import 'package:front_end/features/season/season_bracket_layout.dart';

BracketMatchup _game(String a, String b, String winner) =>
    BracketMatchup(teamA: a, teamB: b, status: 'scheduled', predictedWinner: winner);

/// Each round's matchup indices from top to bottom.
List<List<int>> _rowOrder(BracketSlotLayout layout) => [
      for (final slots in layout.slots)
        (List<int>.generate(slots.length, (i) => i)..sort((a, b) => slots[a].compareTo(slots[b]))),
    ];

void main() {
  test('a reseeded round moves the cards so no connector lines cross', () {
    // The NFC in a real simulated run: 6 and 5 win their Wild Card games,
    // so the 1 seed (bye) meets the 6, whose game sits between the two
    // feeding the other Divisional game.
    final layout = computeBracketSlotLayout([
      BracketRound(round: 'Wild Card', matchups: [_game('s2', 's7', 's2'), _game('s3', 's6', 's6'), _game('s4', 's5', 's5')]),
      BracketRound(round: 'Divisional', matchups: [_game('s1', 's6', 's1'), _game('s2', 's5', 's5')]),
      BracketRound(round: 'Conference Championship', matchups: [_game('s1', 's5', 's5')]),
    ]);

    // 3v6 first, by the 1 seed's game; 2v7 and 4v5 together below it.
    expect(_rowOrder(layout)[0], [1, 0, 2]);
    expect(_rowOrder(layout)[1], [0, 1]);
    expect(layout.slots[1][0], layout.slots[0][1]);
    expect(layout.slots[1][1], (layout.slots[0][0] + layout.slots[0][2]) / 2);
    // Every connector goes from the row it was drawn from.
    for (final c in layout.connections) {
      expect(layout.slots[c.fromRound], contains(c.fromSlot));
      expect(layout.slots[c.toRound], contains(c.toSlot));
    }
  });

  test('a bracket whose lines already run clean keeps its seed order', () {
    // The AFC in the same run: the 4/5 winner meets the 1 seed.
    final layout = computeBracketSlotLayout([
      BracketRound(round: 'Wild Card', matchups: [_game('s2', 's7', 's2'), _game('s3', 's6', 's3'), _game('s4', 's5', 's4')]),
      BracketRound(round: 'Divisional', matchups: [_game('s1', 's4', 's1'), _game('s2', 's3', 's2')]),
      BracketRound(round: 'Conference Championship', matchups: [_game('s1', 's2', 's1')]),
    ]);

    expect(layout.slots[0], [0, 1, 2]);
    expect(layout.slots[1], [2, 0.5]);
  });
}

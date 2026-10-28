import '../../core/models/season_projection.dart';
import 'season_bracket_dimensions.dart';

/// One resolved bracket slot's position, in "slot units" (1 unit = one
/// vertical card-row). Round 0 gets sequential slots 0,1,2,...; each later
/// round's slot is the average of the earlier-round slot(s) its winner
/// traces back to (a bye uses its own round index instead). Matched by
/// winner team id rather than assuming round sizes halve, so a
/// non-halving round (NBA's play-in) still resolves correctly. Every round
/// then gets a dedup pass (see computeBracketSlotLayout) so no two
/// matchups ever land on the same slot.
class BracketSlotLayout {
  const BracketSlotLayout(this.slots, this.connections);

  /// slots[roundIndex][matchupIndex] -> vertical slot position.
  final List<List<double>> slots;
  final List<BracketConnection> connections;
}

class BracketConnection {
  const BracketConnection(this.fromRound, this.fromSlot, this.toRound, this.toSlot);
  final int fromRound;
  final double fromSlot;
  final int toRound;
  final double toSlot;

  /// True when the source is more than one round back (NBA's Play-In
  /// Elimination Game: a card's loser feeds the next round while its
  /// winner skips ahead two rounds). Styled differently in the painter so
  /// it doesn't read as a routing mistake.
  bool get isSkip => toRound - fromRound > 1;
}

/// A matchup's vertical position is based only on sources in the
/// immediately preceding round; a source found further back is drawn as
/// a connector (see _bracketConnectorsForRound) but doesn't drive
/// placement, since averaging it in can scramble a round's canonical
/// seed order.
List<double> _desiredMatchupPositions(
  List<BracketMatchup> matchups, List<BracketMatchup> previousMatchups, List<double> previousSlots,
) {
  final desired = List<double>.filled(matchups.length, 0);
  for (var i = 0; i < matchups.length; i++) {
    final matchup = matchups[i];
    final realSources = <double>[];
    final byeSources = <double>[];
    final currentSides = {matchup.teamA, matchup.teamB}..removeWhere((side) => side == null);
    for (var j = 0; j < previousMatchups.length; j++) {
      final previous = previousMatchups[j];
      final previousSides = {previous.teamA, previous.teamB}..removeWhere((side) => side == null);
      // A bye's own null side is never a "shared side" with another
      // bye's null side -- both were removed above, so this only
      // matches on a real team id appearing in both matchups.
      if (previousSides.intersection(currentSides).isNotEmpty) {
        (previous.teamA != null && previous.teamB != null ? realSources : byeSources).add(previousSlots[j]);
      }
    }
    // A bye source doesn't drive position when a real source is also
    // present -- the bye side never gets a card or a connector (see the
    // backward search below), so averaging its slot in would pull this
    // matchup off the real source's row for no visible reason, forcing
    // an otherwise-unnecessary dogleg into an already-straight
    // single-connector line. Only fall back to the bye's own slot when
    // it's the sole immediate source (no real game to align with).
    final immediateSources = realSources.isNotEmpty ? realSources : byeSources;
    // No traceable source in the immediately preceding round ("bye"
    // into this round) -- its own index stands in until the dedup pass
    // below places it.
    desired[i] = immediateSources.isEmpty
        ? i.toDouble()
        : immediateSources.reduce((a, b) => a + b) / immediateSources.length;
  }
  return desired;
}

/// Assigns final slots by desired position (ties broken by original
/// index), pushing each one at least a full row past the slot just
/// assigned before it -- a full 1-row minimum gap, not just an
/// inequality check, since two desired values can be close enough to
/// overlap visually without being numerically equal.
List<double> _assignRoundSlots(int matchupCount, List<double> desired) {
  final order = List<int>.generate(matchupCount, (i) => i)
    ..sort((a, b) {
      final cmp = desired[a].compareTo(desired[b]);
      return cmp != 0 ? cmp : a.compareTo(b);
    });
  final roundSlots = List<double?>.filled(matchupCount, null);
  var lastAssigned = double.negativeInfinity;
  for (final i in order) {
    final candidate = desired[i] > lastAssigned + 1 ? desired[i] : lastAssigned + 1;
    roundSlots[i] = candidate;
    lastAssigned = candidate;
  }
  return roundSlots.cast<double>();
}

/// Connector lines search back through every earlier round (nearest
/// first, matching either of a previous matchup's two participants),
/// independent of what drove position in _desiredMatchupPositions --
/// draws the real source even for a side that skips the immediately
/// preceding round or that only reappears as the loser of an earlier
/// game.
List<BracketConnection> _bracketConnectorsForRound(
  int r, List<BracketMatchup> matchups, List<BracketRound> rounds, List<List<double>> slots, List<double> roundSlots,
) {
  return [
    for (var i = 0; i < matchups.length; i++) ..._matchupConnections(rounds, slots, matchups[i], r, roundSlots[i]),
  ];
}

/// One matchup's connector lines: for each side, searches back through
/// earlier rounds until it finds the game that side came from.
List<BracketConnection> _matchupConnections(
  List<BracketRound> rounds, List<List<double>> slots, BracketMatchup matchup, int r, double toSlot,
) {
  final connections = <BracketConnection>[];
  for (final side in [matchup.teamA, matchup.teamB]) {
    if (side == null) continue; // A bye side has no earlier-round game to trace a connector back to.
    for (var back = r - 1; back >= 0; back--) {
      final foundIndex = rounds[back].matchups.indexWhere((m) => m.teamA == side || m.teamB == side);
      if (foundIndex != -1) {
        final source = rounds[back].matchups[foundIndex];
        // A bye source has no card rendered for it (see the card loop
        // in _BracketTree) -- nothing to draw a connector line from.
        // The team wasn't playing yet, it was awarded the round
        // automatically; this is where its own bracket path actually
        // starts, so search no further back either.
        if (source.teamA != null && source.teamB != null) {
          connections.add(BracketConnection(back, slots[back][foundIndex], r, toSlot));
        }
        break;
      }
    }
  }
  return connections;
}

/// Indices of the real (non-bye) matchups in `previous` that share a team
/// with `matchup`, ascending.
List<int> _realSourceIndices(BracketMatchup matchup, List<BracketMatchup> previous) {
  final sides = {matchup.teamA, matchup.teamB}..remove(null);
  return [
    for (var j = 0; j < previous.length; j++)
      if (previous[j].teamA != null && previous[j].teamB != null && (sides.contains(previous[j].teamA) || sides.contains(previous[j].teamB)))
        j,
  ];
}

/// True when two matchups' feeder lines from the round before would cross:
/// one sits above the other but is fed from lower down.
bool _hasCrossing(List<BracketRound> rounds, List<List<double>> slots) {
  for (var r = 1; r < rounds.length; r++) {
    final spans = <({double at, double low, double high})>[];
    for (var i = 0; i < rounds[r].matchups.length; i++) {
      final sources = [for (final j in _realSourceIndices(rounds[r].matchups[i], rounds[r - 1].matchups)) slots[r - 1][j]];
      if (sources.isEmpty) continue;
      sources.sort();
      spans.add((at: slots[r][i], low: sources.first, high: sources.last));
    }
    spans.sort((a, b) => a.at.compareTo(b.at));
    for (var k = 1; k < spans.length; k++) {
      if (spans[k - 1].high > spans[k].low) return true;
    }
  }
  return false;
}

/// Each round's matchup indices walked back from the last round, so every
/// matchup's feeders sit together in the order of what they feed. A
/// reseeded round (NFL's 1 seed meeting the lowest seed left) can draw its
/// feeders from anywhere in the round before.
List<List<int>> _treeOrder(List<BracketRound> rounds) {
  final orders = List<List<int>>.filled(rounds.length, const []);
  orders[rounds.length - 1] = [for (var i = 0; i < rounds.last.matchups.length; i++) i];
  for (var r = rounds.length - 1; r > 0; r--) {
    final previous = rounds[r - 1].matchups;
    final placed = <int>{};
    final order = <int>[];
    for (final i in orders[r]) {
      for (final j in _realSourceIndices(rounds[r].matchups[i], previous)) {
        if (placed.add(j)) order.add(j);
      }
    }
    for (var j = 0; j < previous.length; j++) {
      if (placed.add(j)) order.add(j);
    }
    orders[r - 1] = order;
  }
  return orders;
}

/// Slot positions for every matchup, in each round's own matchup order.
/// Rounds stay in their given order unless that would cross connector
/// lines, in which case they're laid out in _treeOrder.
BracketSlotLayout computeBracketSlotLayout(List<BracketRound> rounds) {
  final natural = _layoutInOrder(rounds);
  if (rounds.isEmpty || !_hasCrossing(rounds, natural.slots)) return natural;

  final orders = _treeOrder(rounds);
  final reordered = _layoutInOrder([
    for (var r = 0; r < rounds.length; r++)
      BracketRound(round: rounds[r].round, matchups: [for (final i in orders[r]) rounds[r].matchups[i]]),
  ]);
  return BracketSlotLayout(
    [for (var r = 0; r < rounds.length; r++) _inOriginalOrder(orders[r], reordered.slots[r])],
    reordered.connections,
  );
}

/// `slots` (in `order`'s sequence) put back at each matchup's own index.
List<double> _inOriginalOrder(List<int> order, List<double> slots) {
  final original = List<double>.filled(order.length, 0);
  for (var k = 0; k < order.length; k++) {
    original[order[k]] = slots[k];
  }
  return original;
}

BracketSlotLayout _layoutInOrder(List<BracketRound> rounds) {
  final slots = <List<double>>[];
  final connections = <BracketConnection>[];

  for (var r = 0; r < rounds.length; r++) {
    final matchups = rounds[r].matchups;
    if (r == 0) {
      slots.add([for (var i = 0; i < matchups.length; i++) i.toDouble()]);
      continue;
    }

    final desired = _desiredMatchupPositions(matchups, rounds[r - 1].matchups, slots[r - 1]);
    final roundSlots = _assignRoundSlots(matchups.length, desired);
    connections.addAll(_bracketConnectorsForRound(r, matchups, rounds, slots, roundSlots));
    slots.add(roundSlots);
  }

  return BracketSlotLayout(slots, connections);
}

/// Merges two conferences' independently-computed bracket layouts into one
/// combined layout for the converging tree. Conference B is stacked as a
/// fixed band directly underneath conference A's tallest slot, so neither
/// conference's dedup pass ever sees the other's rows.
({BracketSlotLayout layout, double conferenceBOffset}) computeConferenceBracketLayout(
  List<BracketRound> roundsA,
  List<BracketRound> roundsB,
  BracketMatchup finalMatchup,
) {
  final layoutA = computeBracketSlotLayout(roundsA);
  final layoutB = computeBracketSlotLayout(roundsB);
  final roundCount = roundsA.length < roundsB.length ? roundsA.length : roundsB.length;
  final offset = _maxSlot(layoutA) + 1 + BracketDimensions.labelSeamGapSlots;
  final last = roundCount - 1;

  final slots = [
    for (var r = 0; r < roundCount; r++) [...layoutA.slots[r], for (final slot in layoutB.slots[r]) slot + offset],
  ];
  final connections = [...layoutA.connections, for (final c in layoutB.connections) _shifted(c, offset)];

  // The championship's two sides are each conference's own last-round
  // winner -- converge the championship card between the matchups that
  // produced them.
  final finalSources = [
    _championshipSourceSlot(roundsA[last], layoutA.slots[last], finalMatchup, 0),
    _championshipSourceSlot(roundsB[last], layoutB.slots[last], finalMatchup, offset),
  ].whereType<double>().toList();
  final finalSlot = finalSources.isEmpty ? 0.0 : finalSources.reduce((a, b) => a + b) / finalSources.length;
  connections.addAll([for (final source in finalSources) BracketConnection(last, source, roundCount, finalSlot)]);
  slots.add([finalSlot]);

  return (layout: BracketSlotLayout(slots, connections), conferenceBOffset: offset);
}

/// The layout's lowest (largest) slot, 0 for an empty layout.
double _maxSlot(BracketSlotLayout layout) =>
    layout.slots.expand((round) => round).fold(0.0, (highest, slot) => slot > highest ? slot : highest);

BracketConnection _shifted(BracketConnection c, double offset) =>
    BracketConnection(c.fromRound, c.fromSlot + offset, c.toRound, c.toSlot + offset);

/// The slot (plus `bandOffset`) of the last-round matchup whose winner --
/// actual if final, else predicted -- plays in the championship; null if
/// none does.
double? _championshipSourceSlot(
  BracketRound lastRound, List<double> lastRoundSlots, BracketMatchup finalMatchup, double bandOffset,
) {
  final index = lastRound.matchups.indexWhere((m) {
    final winner = m.isFinal ? m.actualWinner : m.predictedWinner;
    return winner != null && (winner == finalMatchup.teamA || winner == finalMatchup.teamB);
  });
  return index == -1 ? null : lastRoundSlots[index] + bandOffset;
}

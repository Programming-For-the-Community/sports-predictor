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
  final connections = <BracketConnection>[];
  for (var i = 0; i < matchups.length; i++) {
    final matchup = matchups[i];
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
            connections.add(BracketConnection(back, slots[back][foundIndex], r, roundSlots[i]));
          }
          break;
        }
      }
    }
  }
  return connections;
}

BracketSlotLayout computeBracketSlotLayout(List<BracketRound> rounds) {
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

  var maxSlotA = 0.0;
  for (final roundSlots in layoutA.slots) {
    for (final slot in roundSlots) {
      if (slot > maxSlotA) maxSlotA = slot;
    }
  }
  final offset = maxSlotA + 1 + BracketDimensions.labelSeamGapSlots;

  final slots = <List<double>>[];
  final connections = <BracketConnection>[
    ...layoutA.connections,
    for (final c in layoutB.connections) BracketConnection(c.fromRound, c.fromSlot + offset, c.toRound, c.toSlot + offset),
  ];
  for (var r = 0; r < roundCount; r++) {
    slots.add([...layoutA.slots[r], for (final slot in layoutB.slots[r]) slot + offset]);
  }

  // The championship's two sides are each conference's own last-round
  // winner -- trace which matchup produced it so the championship card
  // converges at the right height.
  double? sourceSlot(List<BracketRound> rounds, List<double> lastRoundSlots, double bandOffset) {
    final lastMatchups = rounds[roundCount - 1].matchups;
    for (var j = 0; j < lastMatchups.length; j++) {
      final previous = lastMatchups[j];
      final winner = previous.isFinal ? previous.actualWinner : previous.predictedWinner;
      if (winner != null && (winner == finalMatchup.teamA || winner == finalMatchup.teamB)) {
        return lastRoundSlots[j] + bandOffset;
      }
    }
    return null;
  }

  final finalSources = [
    sourceSlot(roundsA, layoutA.slots[roundCount - 1], 0),
    sourceSlot(roundsB, layoutB.slots[roundCount - 1], offset),
  ].whereType<double>().toList();
  final finalSlot = finalSources.isEmpty ? 0.0 : finalSources.reduce((a, b) => a + b) / finalSources.length;
  for (final source in finalSources) {
    connections.add(BracketConnection(roundCount - 1, source, roundCount, finalSlot));
  }
  slots.add([finalSlot]);

  return (layout: BracketSlotLayout(slots, connections), conferenceBOffset: offset);
}

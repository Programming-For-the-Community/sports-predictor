import 'package:flutter/material.dart';

import '../../core/models/season_projection.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import 'season_bracket_dimensions.dart';
import 'season_bracket_geometry.dart';
import 'season_bracket_layout.dart';
import 'season_bracket_matchup_card.dart';
import 'season_bracket_round_labels.dart';
import 'season_championship_card.dart';
import 'season_first_four_section.dart';
import 'season_horizontal_scrollable_bracket.dart';

/// The largest slot value across both halves' own layouts -- used to size
/// the grid's total height. Pulled out of MarchMadnessGrid.build so its
/// own triple-nested loop doesn't count toward that function's cognitive
/// complexity.
double _maxSlotAcross(List<BracketSlotLayout> layouts) {
  var maxSlot = 0.0;
  for (final layout in layouts) {
    for (final roundSlots in layout.slots) {
      for (final slot in roundSlots) {
        if (slot > maxSlot) maxSlot = slot;
      }
    }
  }
  return maxSlot;
}

/// Round r's matchups, in the same left-then-right-region concatenation
/// order computeConferenceBracketLayout used to build a half's own
/// layout -- index i into a round's slots always lines up with index i
/// here. Round halfColumns - 1 is the appended Final Four round, a single
/// matchup outside either region's own rounds.
List<BracketMatchup> _roundMatchups(
  List<List<BracketRound>> regionRounds, int r, int halfColumns, BracketMatchup finalFourMatchup,
) {
  if (r < halfColumns - 1) return [...regionRounds[0][r].matchups, ...regionRounds[1][r].matchups];
  return [finalFourMatchup];
}

String _roundLabel(List<BracketRound> regionRounds, int r, int halfColumns) {
  if (r < halfColumns - 1) return regionRounds[r].round;
  return 'Final Four';
}

/// One half's own region-round + Final-Four cards, positioned via the
/// given column/slot functions -- the double-nested loop this replaces
/// (round, then each round's own matchups) is identical for the left and
/// right halves, differing only in which column/slot-layout/matchup
/// lookup each side passes in.
List<Widget> _regionCards(
  int halfColumns,
  List<List<double>> slots,
  List<BracketMatchup> Function(int round) roundMatchups,
  double Function(int round) column,
  Widget Function(BracketMatchup matchup, double column, double slot) card,
) {
  return [
    for (var r = 0; r < halfColumns; r++)
      for (var i = 0; i < slots[r].length; i++) card(roundMatchups(r)[i], column(r), slots[r][i]),
  ];
}

/// Each First Four game's predicted winner already holds a fixed, known
/// Round-of-64 slot (see season_projection.py's own
/// _march_madness_bracket_payload docstring -- region assignment is
/// seeded off that same predicted winner) -- finds which side's
/// Round-of-64 list contains it and at what index, so the card can be
/// drawn at that row instead of in an unpositioned list. A game whose
/// winner isn't found on either side (should not happen for a
/// self-consistent payload) lands in `unresolved` instead of crashing on
/// a missing match.
({
  List<({BracketMatchup matchup, ({bool isLeft, int index}) destination})> placements,
  List<BracketMatchup> unresolved,
}) _resolveFirstFourPlacements(
  List<BracketMatchup> firstFour,
  List<BracketMatchup> Function(int round) leftRoundMatchups,
  List<BracketMatchup> Function(int round) rightRoundMatchups,
) {
  ({bool isLeft, int index})? locate(BracketMatchup matchup) {
    final winner = matchup.predictedWinner;
    if (winner == null) return null;
    final leftIndex = leftRoundMatchups(0).indexWhere((m) => m.teamA == winner || m.teamB == winner);
    if (leftIndex != -1) return (isLeft: true, index: leftIndex);
    final rightIndex = rightRoundMatchups(0).indexWhere((m) => m.teamA == winner || m.teamB == winner);
    if (rightIndex != -1) return (isLeft: false, index: rightIndex);
    return null;
  }

  return (
    placements: [
      for (final matchup in firstFour)
        if (locate(matchup) case final destination?) (matchup: matchup, destination: destination),
    ],
    unresolved: [
      for (final matchup in firstFour)
        if (locate(matchup) == null) matchup,
    ],
  );
}

/// Resolves one half's own BracketConnection list (round/slot, as
/// computeConferenceBracketLayout produced it) into absolute-coordinate
/// elbow segments. `mirrored: false` exits a source card's right edge and
/// enters a destination card's left edge (round index increases
/// left-to-right, same as _BracketConnectorPainter); `mirrored: true`
/// exits the source's left edge and enters the destination's right edge
/// (round index increases right-to-left, since the source round sits to
/// the destination round's right in the mirrored half).
List<GridSegment> _sideSegments(
  List<BracketConnection> connections,
  double Function(int round) column,
  double Function(double slot) yCenter, {
  required bool mirrored,
}) {
  final segments = <GridSegment>[];
  for (final c in connections) {
    final fromX = column(c.fromRound) * MarchMadnessGrid._columnWidth + (mirrored ? 0 : BracketDimensions.cardWidth);
    final toX = column(c.toRound) * MarchMadnessGrid._columnWidth + (mirrored ? BracketDimensions.cardWidth : 0);
    segments.addAll(elbow(Offset(fromX, yCenter(c.fromSlot)), Offset(toX, yCenter(c.toSlot)), dashed: c.isSkip));
  }
  return segments;
}

class _GridConnectorPainter extends CustomPainter {
  const _GridConnectorPainter({required this.segments, required this.color});
  final List<GridSegment> segments;
  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = color
      ..strokeWidth = 1.5
      ..style = PaintingStyle.stroke;
    for (final segment in segments) {
      if (!segment.dashed) {
        canvas.drawLine(segment.from, segment.to, paint);
        continue;
      }
      const dashLength = 5.0;
      const gapLength = 4.0;
      final total = (segment.to - segment.from).distance;
      if (total == 0) continue;
      final direction = (segment.to - segment.from) / total;
      var walked = 0.0;
      while (walked < total) {
        final segmentEnd = (walked + dashLength).clamp(0.0, total);
        canvas.drawLine(segment.from + direction * walked, segment.from + direction * segmentEnd, paint);
        walked += dashLength + gapLength;
      }
    }
  }

  @override
  bool shouldRepaint(covariant _GridConnectorPainter oldDelegate) => oldDelegate.segments != segments;
}

/// The traditional 4-quadrant March Madness grid: regionOrder[0]/[1]
/// converge left-to-right into a Final Four card on the left half,
/// regionOrder[2]/[3] converge right-to-left (mirrored -- Round of 64 on
/// the outer/right edge, converging inward) into a Final Four card on the
/// right half, and both Final Four winners meet at a single Championship
/// card in the middle -- the same shape a printed bracket uses, unlike
/// BracketTree's single left-to-right tree (which is right for every
/// other sport's bracket, none of which have 2 sides converging toward a
/// shared center).
///
/// Each half reuses computeConferenceBracketLayout unchanged (it already
/// computes exactly "2 sources converge to 1 final matchup" -- the same
/// shape NFL/NBA's own conference-split bracket needs); only the mapping
/// from round index to horizontal position differs per half.
class MarchMadnessGrid extends StatelessWidget {
  const MarchMadnessGrid({super.key, required this.sport, required this.bracket, required this.regionOrder});

  final String sport;
  final MarchMadnessBracket bracket;
  final List<String> regionOrder;

  static const double _columnWidth = BracketDimensions.cardWidth + BracketDimensions.roundGap;

  @override
  Widget build(BuildContext context) {
    final grid = _GridLayout(bracket, regionOrder);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (grid.unresolvedFirstFour.isNotEmpty) ...[
          FirstFourSection(sport: sport, matchups: grid.unresolvedFirstFour, bracket: bracket),
          const SizedBox(height: 20),
        ],
        HorizontalScrollableBracket(
          width: grid.totalWidth,
          height: grid.totalHeight + 2 * BracketDimensions.headerHeight + 10,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              SizedBox(height: BracketDimensions.headerHeight, child: _headers(grid)),
              const SizedBox(height: 10 + BracketDimensions.headerHeight),
              SizedBox(width: grid.totalWidth, height: grid.totalHeight, child: _cards(grid)),
            ],
          ),
        ),
      ],
    );
  }

  Widget _headers(_GridLayout grid) => Stack(
        clipBehavior: Clip.none,
        children: [
          if (grid.hasLeftFirstFour) _roundHeader(grid, BracketRoundLabels.firstFour, _GridLayout.firstFourLeftColumn),
          for (var r = 0; r < grid.halfColumns; r++) _roundHeader(grid, grid.roundLabel(grid.leftRegionRounds[0], r), grid.leftColumn(r)),
          for (var r = 0; r < grid.halfColumns; r++) _roundHeader(grid, grid.roundLabel(grid.rightRegionRounds[0], r), grid.rightColumn(r)),
          if (grid.hasRightFirstFour) _roundHeader(grid, BracketRoundLabels.firstFour, grid.firstFourRightColumn),
          _roundHeader(grid, BracketRoundLabels.championship, grid.championshipColumn, width: BracketDimensions.championshipCardWidth),
        ],
      );

  Widget _cards(_GridLayout grid) => Stack(
        clipBehavior: Clip.none,
        children: [
          _regionLabel(grid, regionOrder[0], grid.leftColumn(0), 0),
          _regionLabel(grid, regionOrder[1], grid.leftColumn(0), grid.left.conferenceBOffset),
          _regionLabel(grid, regionOrder[2], grid.rightColumn(0), 0),
          _regionLabel(grid, regionOrder[3], grid.rightColumn(0), grid.right.conferenceBOffset),
          Positioned.fill(child: CustomPaint(painter: _GridConnectorPainter(segments: grid.segments(), color: AppColors.inkSub))),
          ..._regionCards(grid.halfColumns, grid.left.layout.slots, grid.leftRoundMatchups, grid.leftColumn, (m, c, s) => _card(grid, m, c, s)),
          ..._regionCards(grid.halfColumns, grid.right.layout.slots, grid.rightRoundMatchups, grid.rightColumn, (m, c, s) => _card(grid, m, c, s)),
          for (final placement in grid.firstFourPlacements)
            _card(grid, placement.matchup, grid.firstFourColumn(placement.destination), grid.destinationSlot(placement.destination)),
          Positioned(
            left: grid.championshipLeft,
            top: grid.championshipTop,
            width: BracketDimensions.championshipCardWidth,
            height: BracketDimensions.championshipCardHeight,
            child: ChampionshipCard(sport: sport, matchup: bracket.championship!, teamNames: bracket.teamNames),
          ),
        ],
      );

  Widget _regionLabel(_GridLayout grid, String name, double column, double slot) => Positioned(
        left: grid.x(column),
        top: grid.y(slot) - BracketDimensions.labelClearance,
        width: BracketDimensions.cardWidth,
        child: Text(name.toUpperCase(), style: AppTextStyles.microLabel(color: AppColors.cyan), maxLines: 1, overflow: TextOverflow.ellipsis),
      );

  Widget _roundHeader(_GridLayout grid, String label, double column, {double width = BracketDimensions.cardWidth}) => Positioned(
        left: grid.x(column),
        width: width,
        child: Text(label.toUpperCase(), style: AppTextStyles.microLabel(), maxLines: 1, overflow: TextOverflow.ellipsis),
      );

  Widget _card(_GridLayout grid, BracketMatchup matchup, double column, double slot) => Positioned(
        left: grid.x(column),
        top: grid.y(slot),
        width: BracketDimensions.cardWidth,
        height: BracketDimensions.cardHeight,
        child: BracketMatchupCard(sport: sport, matchup: matchup, teamNames: bracket.teamNames, cardWidth: BracketDimensions.cardWidth),
      );
}

typedef _Destination = ({bool isLeft, int index});

/// The grid's geometry, worked out once per build: both halves' layouts,
/// which column each round sits in, where the First Four and championship
/// cards go, and the grid's total size.
class _GridLayout {
  _GridLayout(MarchMadnessBracket bracket, List<String> regionOrder)
      : leftRegionRounds = [bracket.regions[regionOrder[0]]!.rounds, bracket.regions[regionOrder[1]]!.rounds],
        rightRegionRounds = [bracket.regions[regionOrder[2]]!.rounds, bracket.regions[regionOrder[3]]!.rounds],
        _leftFinalFour = bracket.finalFour[0],
        _rightFinalFour = bracket.finalFour[1],
        // First Four cards get their own outer column on whichever side they
        // feed (see _resolveFirstFourPlacements) -- reserved on both sides
        // whenever any First Four game exists, rather than computed per side,
        // so the grid's own column math doesn't depend on which specific
        // regions happen to draw a First Four game this run.
        hasFirstFour = bracket.firstFour.isNotEmpty {
    left = computeConferenceBracketLayout(leftRegionRounds[0], leftRegionRounds[1], _leftFinalFour);
    right = computeConferenceBracketLayout(rightRegionRounds[0], rightRegionRounds[1], _rightFinalFour);
    final resolution = _resolveFirstFourPlacements(bracket.firstFour, leftRoundMatchups, rightRoundMatchups);
    firstFourPlacements = resolution.placements;
    unresolvedFirstFour = resolution.unresolved;
  }

  static const firstFourLeftColumn = 0.0;

  final List<List<BracketRound>> leftRegionRounds;
  final List<List<BracketRound>> rightRegionRounds;
  final BracketMatchup _leftFinalFour;
  final BracketMatchup _rightFinalFour;
  final bool hasFirstFour;
  late final ({BracketSlotLayout layout, double conferenceBOffset}) left;
  late final ({BracketSlotLayout layout, double conferenceBOffset}) right;
  late final List<({BracketMatchup matchup, _Destination destination})> firstFourPlacements;
  late final List<BracketMatchup> unresolvedFirstFour;

  // Region rounds (Round of 64 .. Elite Eight) plus the Final Four round
  // computeConferenceBracketLayout appends -- both halves share this
  // shape since every region is a fixed 16-team, no-bye field.
  int get halfColumns => leftRegionRounds[0].length + 1;
  int get _columnOffset => hasFirstFour ? 1 : 0;
  double get championshipColumn => (halfColumns + _columnOffset).toDouble();
  double get firstFourRightColumn => (halfColumns * 2 + _columnOffset + 1).toDouble();
  bool get hasLeftFirstFour => firstFourPlacements.any((p) => p.destination.isLeft);
  bool get hasRightFirstFour => firstFourPlacements.any((p) => !p.destination.isLeft);

  double leftColumn(int round) => (round + _columnOffset).toDouble();
  double rightColumn(int round) => (halfColumns * 2 - round + _columnOffset).toDouble();
  double x(double column) => column * MarchMadnessGrid._columnWidth;
  double y(double slot) => slot * BracketDimensions.verticalUnit;
  double yCenter(double slot) => y(slot) + BracketDimensions.cardHeight / 2;

  List<BracketMatchup> leftRoundMatchups(int r) => _roundMatchups(leftRegionRounds, r, halfColumns, _leftFinalFour);
  List<BracketMatchup> rightRoundMatchups(int r) => _roundMatchups(rightRegionRounds, r, halfColumns, _rightFinalFour);
  String roundLabel(List<BracketRound> regionRounds, int r) => _roundLabel(regionRounds, r, halfColumns);

  double get _leftFinalFourSlot => left.layout.slots[halfColumns - 1][0];
  double get _rightFinalFourSlot => right.layout.slots[halfColumns - 1][0];
  double get _championshipSlot => (_leftFinalFourSlot + _rightFinalFourSlot) / 2;

  // Centered on the same column/slot a same-size card would use, just scaled
  // up around that center point -- the connector elbows entering it need its
  // actual (shifted) edges, not the standard-card-width position the rest of
  // the grid's columns use.
  double get championshipLeft =>
      x(championshipColumn) - (BracketDimensions.championshipCardWidth - BracketDimensions.cardWidth) / 2;
  double get championshipTop =>
      y(_championshipSlot) - (BracketDimensions.championshipCardHeight - BracketDimensions.cardHeight) / 2;

  double get totalWidth =>
      ((hasFirstFour ? firstFourRightColumn : rightColumn(0)) + 1) * MarchMadnessGrid._columnWidth - BracketDimensions.roundGap;
  double get totalHeight =>
      _maxSlotAcross([left.layout, right.layout]) * BracketDimensions.verticalUnit + BracketDimensions.cardHeight;

  /// The column a First Four card sits in: the outer column on its side.
  double firstFourColumn(_Destination destination) => destination.isLeft ? firstFourLeftColumn : firstFourRightColumn;

  /// The Round-of-64 slot a First Four winner feeds.
  double destinationSlot(_Destination destination) =>
      (destination.isLeft ? left : right).layout.slots[0][destination.index];

  /// Every connector line: both halves, the two Final Fours into the
  /// championship, and each First Four card into its Round-of-64 slot.
  List<GridSegment> segments() => [
        ..._sideSegments(left.layout.connections, leftColumn, yCenter, mirrored: false),
        ..._sideSegments(right.layout.connections, rightColumn, yCenter, mirrored: true),
        ...elbow(
          Offset(x(leftColumn(halfColumns - 1)) + BracketDimensions.cardWidth, yCenter(_leftFinalFourSlot)),
          Offset(championshipLeft, yCenter(_championshipSlot)),
          dashed: false,
        ),
        ...elbow(
          Offset(x(rightColumn(halfColumns - 1)), yCenter(_rightFinalFourSlot)),
          Offset(championshipLeft + BracketDimensions.championshipCardWidth, yCenter(_championshipSlot)),
          dashed: false,
        ),
        for (final placement in firstFourPlacements) ..._firstFourConnector(placement.destination),
      ];

  /// A First Four card's connector: out its inner edge, into its Round-of-64
  /// card's outer edge.
  List<GridSegment> _firstFourConnector(_Destination destination) {
    final slotY = yCenter(destinationSlot(destination));
    if (destination.isLeft) {
      return elbow(Offset(x(firstFourLeftColumn) + BracketDimensions.cardWidth, slotY), Offset(x(leftColumn(0)), slotY), dashed: false);
    }
    return elbow(Offset(x(firstFourRightColumn), slotY), Offset(x(rightColumn(0)) + BracketDimensions.cardWidth, slotY), dashed: false);
  }
}

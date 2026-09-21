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
    final leftRegionRounds = [bracket.regions[regionOrder[0]]!.rounds, bracket.regions[regionOrder[1]]!.rounds];
    final rightRegionRounds = [bracket.regions[regionOrder[2]]!.rounds, bracket.regions[regionOrder[3]]!.rounds];

    final left = computeConferenceBracketLayout(leftRegionRounds[0], leftRegionRounds[1], bracket.finalFour[0]);
    final right = computeConferenceBracketLayout(rightRegionRounds[0], rightRegionRounds[1], bracket.finalFour[1]);

    // Region rounds (Round of 64 .. Elite Eight) plus the Final Four round
    // computeConferenceBracketLayout appends -- both halves share this
    // shape since every region is a fixed 16-team, no-bye field.
    final halfColumns = leftRegionRounds[0].length + 1;
    // First Four cards get their own outer column on whichever side they
    // feed (see _resolveFirstFourPlacements) -- reserved on both sides
    // whenever any First Four game exists, rather than computed per side,
    // so the grid's own column math doesn't depend on which specific
    // regions happen to draw a First Four game this run.
    final hasFirstFour = bracket.firstFour.isNotEmpty;
    final columnOffset = hasFirstFour ? 1 : 0;
    final championshipColumn = halfColumns + columnOffset;
    const firstFourLeftColumn = 0.0;
    final firstFourRightColumn = (halfColumns * 2 + columnOffset + 1).toDouble();
    double leftColumn(int round) => (round + columnOffset).toDouble();
    double rightColumn(int round) => (halfColumns * 2 - round + columnOffset).toDouble();
    double x(double column) => column * _columnWidth;
    double y(double slot) => slot * BracketDimensions.verticalUnit;
    double yCenter(double slot) => y(slot) + BracketDimensions.cardHeight / 2;

    final maxSlot = _maxSlotAcross([left.layout, right.layout]);

    final leftFinalFourSlot = left.layout.slots[halfColumns - 1][0];
    final rightFinalFourSlot = right.layout.slots[halfColumns - 1][0];
    final championshipSlot = (leftFinalFourSlot + rightFinalFourSlot) / 2;

    List<BracketMatchup> leftRoundMatchups(int r) => _roundMatchups(leftRegionRounds, r, halfColumns, bracket.finalFour[0]);
    List<BracketMatchup> rightRoundMatchups(int r) => _roundMatchups(rightRegionRounds, r, halfColumns, bracket.finalFour[1]);
    String roundLabel(List<BracketRound> regionRounds, int r) => _roundLabel(regionRounds, r, halfColumns);

    final firstFourResolution = _resolveFirstFourPlacements(bracket.firstFour, leftRoundMatchups, rightRoundMatchups);
    final firstFourPlacements = firstFourResolution.placements;
    final unresolvedFirstFour = firstFourResolution.unresolved;
    final hasLeftFirstFour = firstFourPlacements.any((p) => p.destination.isLeft);
    final hasRightFirstFour = firstFourPlacements.any((p) => !p.destination.isLeft);

    // Centered on the same column/slot a same-size card would use, just
    // scaled up around that center point -- computed here (not just
    // where it's used to position the card widget below) since the
    // connector elbows entering it below need its own actual (shifted)
    // edges too, not the unshifted standard-card-width position the rest
    // of this grid's columns use.
    final championshipLeft = x(championshipColumn.toDouble()) - (BracketDimensions.championshipCardWidth - BracketDimensions.cardWidth) / 2;
    final championshipTop = y(championshipSlot) - (BracketDimensions.championshipCardHeight - BracketDimensions.cardHeight) / 2;

    final segments = <GridSegment>[
      ..._sideSegments(left.layout.connections, leftColumn, yCenter, mirrored: false),
      ..._sideSegments(right.layout.connections, rightColumn, yCenter, mirrored: true),
      ...elbow(
        Offset(x(leftColumn(halfColumns - 1)) + BracketDimensions.cardWidth, yCenter(leftFinalFourSlot)),
        Offset(championshipLeft, yCenter(championshipSlot)),
        dashed: false,
      ),
      ...elbow(
        Offset(x(rightColumn(halfColumns - 1)), yCenter(rightFinalFourSlot)),
        Offset(championshipLeft + BracketDimensions.championshipCardWidth, yCenter(championshipSlot)),
        dashed: false,
      ),
      for (final placement in firstFourPlacements)
        ...elbow(
          placement.destination.isLeft
              ? Offset(x(firstFourLeftColumn) + BracketDimensions.cardWidth, yCenter(left.layout.slots[0][placement.destination.index]))
              : Offset(x(firstFourRightColumn), yCenter(right.layout.slots[0][placement.destination.index])),
          placement.destination.isLeft
              ? Offset(x(leftColumn(0)), yCenter(left.layout.slots[0][placement.destination.index]))
              : Offset(x(rightColumn(0)) + BracketDimensions.cardWidth, yCenter(right.layout.slots[0][placement.destination.index])),
          dashed: false,
        ),
    ];

    final rightmostColumn = hasFirstFour ? firstFourRightColumn : rightColumn(0);
    final totalWidth = (rightmostColumn + 1) * _columnWidth - BracketDimensions.roundGap;
    final totalHeight = maxSlot * BracketDimensions.verticalUnit + BracketDimensions.cardHeight;

    Widget regionLabel(String name, double column, double slot) => Positioned(
          left: x(column),
          top: y(slot) - BracketDimensions.labelClearance,
          width: BracketDimensions.cardWidth,
          child: Text(name.toUpperCase(), style: AppTextStyles.microLabel(color: AppColors.cyan), maxLines: 1, overflow: TextOverflow.ellipsis),
        );

    Widget roundHeader(String label, double column, {double width = BracketDimensions.cardWidth}) => Positioned(
          left: x(column),
          width: width,
          child: Text(label.toUpperCase(), style: AppTextStyles.microLabel(), maxLines: 1, overflow: TextOverflow.ellipsis),
        );

    Widget card(BracketMatchup matchup, double column, double slot) => Positioned(
          left: x(column),
          top: y(slot),
          width: BracketDimensions.cardWidth,
          height: BracketDimensions.cardHeight,
          child: BracketMatchupCard(sport: sport, matchup: matchup, teamNames: bracket.teamNames, cardWidth: BracketDimensions.cardWidth),
        );

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (unresolvedFirstFour.isNotEmpty) ...[
          FirstFourSection(sport: sport, matchups: unresolvedFirstFour, bracket: bracket),
          const SizedBox(height: 20),
        ],
        HorizontalScrollableBracket(
          width: totalWidth,
          height: totalHeight + 2 * BracketDimensions.headerHeight + 10,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              SizedBox(
                height: BracketDimensions.headerHeight,
                child: Stack(
                  clipBehavior: Clip.none,
                  children: [
                    if (hasLeftFirstFour) roundHeader(BracketRoundLabels.firstFour, firstFourLeftColumn),
                    for (var r = 0; r < halfColumns; r++) roundHeader(roundLabel(leftRegionRounds[0], r), leftColumn(r)),
                    for (var r = 0; r < halfColumns; r++) roundHeader(roundLabel(rightRegionRounds[0], r), rightColumn(r)),
                    if (hasRightFirstFour) roundHeader(BracketRoundLabels.firstFour, firstFourRightColumn),
                    roundHeader(BracketRoundLabels.championship, championshipColumn.toDouble(), width: BracketDimensions.championshipCardWidth),
                  ],
                ),
              ),
              const SizedBox(height: 10 + BracketDimensions.headerHeight),
              SizedBox(
                width: totalWidth,
                height: totalHeight,
                child: Stack(
                  clipBehavior: Clip.none,
                  children: [
                    regionLabel(regionOrder[0], leftColumn(0), 0),
                    regionLabel(regionOrder[1], leftColumn(0), left.conferenceBOffset),
                    regionLabel(regionOrder[2], rightColumn(0), 0),
                    regionLabel(regionOrder[3], rightColumn(0), right.conferenceBOffset),
                    Positioned.fill(child: CustomPaint(painter: _GridConnectorPainter(segments: segments, color: AppColors.inkSub))),
                    ..._regionCards(halfColumns, left.layout.slots, leftRoundMatchups, leftColumn, card),
                    ..._regionCards(halfColumns, right.layout.slots, rightRoundMatchups, rightColumn, card),
                    for (final placement in firstFourPlacements)
                      card(
                        placement.matchup,
                        placement.destination.isLeft ? firstFourLeftColumn : firstFourRightColumn,
                        placement.destination.isLeft
                            ? left.layout.slots[0][placement.destination.index]
                            : right.layout.slots[0][placement.destination.index],
                      ),
                    Positioned(
                      left: championshipLeft,
                      top: championshipTop,
                      width: BracketDimensions.championshipCardWidth,
                      height: BracketDimensions.championshipCardHeight,
                      child: ChampionshipCard(sport: sport, matchup: bracket.championship!, teamNames: bracket.teamNames),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

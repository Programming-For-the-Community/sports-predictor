import 'package:flutter/material.dart';

import '../../core/models/season_projection.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import 'season_bracket_dimensions.dart';
import 'season_bracket_geometry.dart';
import 'season_bracket_layout.dart';
import 'season_bracket_matchup_card.dart';
import 'season_championship_card.dart';
import 'season_horizontal_scrollable_bracket.dart';

/// Draws each round-to-round connector as a 3-segment elbow (horizontal out
/// of the source card, vertical to the target's row, horizontal into the
/// target card), positioned from computeBracketSlotLayout's slot math.
///
/// A skip connection (see BracketConnection.isSkip) is drawn dashed, in
/// `skipColor` instead of `color`, so a card feeding two different
/// destinations reads as intentional rather than a mistake.
class _BracketConnectorPainter extends CustomPainter {
  const _BracketConnectorPainter({
    required this.connections,
    required this.color,
    required this.skipColor,
    required this.cardWidth,
    required this.cardHeight,
    required this.roundGap,
    required this.verticalUnit,
    this.championshipRound,
    this.championshipShift = 0,
    this.championshipExtraGap = 0,
  });

  final List<BracketConnection> connections;
  final Color color;
  final Color skipColor;
  final double cardWidth;
  final double cardHeight;
  final double roundGap;
  final double verticalUnit;

  /// The Championship card (see _BracketTree's own isChampionshipRound)
  /// is wider than every other card and centered on its own slot, so its
  /// actual left edge sits championshipShift px earlier than the
  /// standard round-index formula below would place it -- without this,
  /// a connection ending there stopped at the OLD (unshifted) x, which
  /// now lands inside the card's own enlarged bounds instead of at its
  /// edge, reading as the line just touching the card rather than
  /// leading cleanly into it.
  final int? championshipRound;
  final double championshipShift;

  /// _BracketTree's own _championshipEntryGap -- extra room before the
  /// Championship column on top of championshipShift already eating into
  /// the ordinary gap, so the connector legs leading into the card have
  /// enough length to actually read as a line rather than a stub.
  final double championshipExtraGap;

  double _x(int round) =>
      round * (cardWidth + roundGap) + (round == championshipRound ? championshipExtraGap - championshipShift : 0);
  double _y(double slot) => slot * verticalUnit + cardHeight / 2;

  void _drawSegment(Canvas canvas, Offset from, Offset to, Paint paint, {required bool dashed}) {
    if (!dashed) {
      canvas.drawLine(from, to, paint);
      return;
    }
    const dashLength = 5.0;
    const gapLength = 4.0;
    final total = (to - from).distance;
    if (total == 0) return;
    final direction = (to - from) / total;
    var walked = 0.0;
    while (walked < total) {
      final segmentEnd = (walked + dashLength).clamp(0.0, total);
      canvas.drawLine(from + direction * walked, from + direction * segmentEnd, paint);
      walked += dashLength + gapLength;
    }
  }

  @override
  void paint(Canvas canvas, Size size) {
    final normalPaint = Paint()
      ..color = color
      ..strokeWidth = 1.5
      ..style = PaintingStyle.stroke;
    final skipPaint = Paint()
      ..color = skipColor
      ..strokeWidth = 1.5
      ..style = PaintingStyle.stroke;
    for (final connection in connections) {
      final x1 = _x(connection.fromRound) + cardWidth;
      final y1 = _y(connection.fromSlot);
      final x2 = _x(connection.toRound);
      final y2 = _y(connection.toSlot);
      final paint = connection.isSkip ? skipPaint : normalPaint;
      // elbow's own midpoint is (from.dx + to.dx) / 2 -- the real
      // midpoint, not x1 + roundGap / 2 (this painter's own old formula,
      // which assumed x2 - x1 always equals roundGap; true for every
      // ordinary round-to-round gap but not one entering the Championship
      // card, which sits championshipShift px closer than a standard
      // card would, see _x above). That old formula put the midpoint
      // PAST the card's own actual edge, collapsing the final leg
      // leading into the card down to just a couple px -- visually
      // indistinguishable from "the connector's vertical run just
      // touches the card," not a clean line leading to it.
      for (final segment in elbow(Offset(x1, y1), Offset(x2, y2), dashed: connection.isSkip)) {
        _drawSegment(canvas, segment.from, segment.to, paint, dashed: segment.dashed);
      }
    }
  }

  @override
  bool shouldRepaint(covariant _BracketConnectorPainter oldDelegate) => oldDelegate.connections != connections;
}

/// Small dashed-line swatch for the skip-connection legend, reusing the
/// connector painter's dash geometry so the sample matches the real lines.
class _DashedLegendSwatchPainter extends CustomPainter {
  const _DashedLegendSwatchPainter({required this.color});
  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = color
      ..strokeWidth = 1.5
      ..style = PaintingStyle.stroke;
    const dashLength = 5.0;
    const gapLength = 4.0;
    final y = size.height / 2;
    var x = 0.0;
    while (x < size.width) {
      final segmentEnd = (x + dashLength).clamp(0.0, size.width);
      canvas.drawLine(Offset(x, y), Offset(segmentEnd, y), paint);
      x += dashLength + gapLength;
    }
  }

  @override
  bool shouldRepaint(covariant _DashedLegendSwatchPainter oldDelegate) => oldDelegate.color != color;
}

/// Converging bracket tree -- rounds placed left to right, each matchup
/// card positioned by its computed slot, connector lines drawn underneath.
/// Horizontally scrollable; the outer page already scrolls vertically, so a
/// fixed-height inner Stack (sized to the widest round's card count) never
/// throws on overflow the way a Row/Column would.
class BracketTree extends StatelessWidget {
  const BracketTree({
    super.key,
    required this.sport,
    required this.rounds,
    required this.teamNames,
    this.conferenceLabels = const [],
    this.precomputedLayout,
    this.highlightFinalMatchup = false,
  });

  final String sport;
  final List<BracketRound> rounds;
  final Map<String, BracketTeamName> teamNames;

  /// Per-conference labels for a combined conference-split tree; each names
  /// the round-0 slot where that conference's band of matchups starts.
  final List<({String name, double slot})> conferenceLabels;

  /// True when `rounds`' own last round is genuinely this bracket's
  /// overall championship (Super Bowl/NBA Finals/National Championship/
  /// NBA Cup Championship) -- renders that one card via ChampionshipCard
  /// instead of BracketMatchupCard. False for a sub-bracket whose own
  /// last round is a real result but not THE championship (a single
  /// conference's own path when there's no shared final to combine into,
  /// or one March Madness region's own Elite Eight) -- set explicitly by
  /// each caller rather than inferred from round position, since
  /// "last round, one matchup" alone can't tell those cases apart.
  final bool highlightFinalMatchup;

  /// Precomputed layout for a combined conference-split tree (see
  /// computeConferenceBracketLayout). Null for the flat (NCAAFB) and
  /// independent-tree fallback cases, which compute their own from
  /// `rounds` below.
  final BracketSlotLayout? precomputedLayout;

  // A bye matchup (either side null -- see BracketMatchup's own doc
  // comment) gets no card at all: the team it awarded simply appears
  // already present in its own next real game, same as
  // computeBracketSlotLayout's own connector search already skips
  // drawing a line back to a bye (nothing to connect to). Pulled out of
  // build so its own nested loop+if/else doesn't count toward that
  // function's cognitive complexity.
  List<Widget> _cards(
    BracketSlotLayout layout, bool isChampionshipRound,
    double championshipExtraWidth, double championshipExtraHeight, double championshipEntryGap,
  ) {
    return [
      for (var r = 0; r < rounds.length; r++)
        for (var i = 0; i < rounds[r].matchups.length; i++)
          if (rounds[r].matchups[i].teamA != null && rounds[r].matchups[i].teamB != null)
            if (isChampionshipRound && r == rounds.length - 1 && i == 0)
              Positioned(
                left: r * (BracketDimensions.cardWidth + BracketDimensions.roundGap) + championshipEntryGap - championshipExtraWidth,
                top: layout.slots[r][i] * BracketDimensions.verticalUnit - championshipExtraHeight,
                width: BracketDimensions.championshipCardWidth,
                height: BracketDimensions.championshipCardHeight,
                child: ChampionshipCard(sport: sport, matchup: rounds[r].matchups[i], teamNames: teamNames),
              )
            else
              Positioned(
                left: r * (BracketDimensions.cardWidth + BracketDimensions.roundGap),
                top: layout.slots[r][i] * BracketDimensions.verticalUnit,
                width: BracketDimensions.cardWidth,
                height: BracketDimensions.cardHeight,
                child: BracketMatchupCard(
                  sport: sport,
                  matchup: rounds[r].matchups[i],
                  teamNames: teamNames,
                  cardWidth: BracketDimensions.cardWidth,
                ),
              ),
    ];
  }

  ({double maxSlot, double championshipExtraWidth, double championshipExtraHeight, double championshipEntryGap})
      _layoutMetrics(BracketSlotLayout layout, bool isChampionshipRound) {
    var maxSlot = 0.0;
    for (final roundSlots in layout.slots) {
      for (final slot in roundSlots) {
        if (slot > maxSlot) maxSlot = slot;
      }
    }
    return (
      maxSlot: maxSlot,
      championshipExtraWidth: isChampionshipRound ? (BracketDimensions.championshipCardWidth - BracketDimensions.cardWidth) / 2 : 0.0,
      championshipExtraHeight: isChampionshipRound ? (BracketDimensions.championshipCardHeight - BracketDimensions.cardHeight) / 2 : 0.0,
      championshipEntryGap: isChampionshipRound ? BracketDimensions.championshipEntryGap : 0.0,
    );
  }

  @override
  Widget build(BuildContext context) {
    if (rounds.isEmpty) {
      return Text('Bracket not available yet.', style: AppTextStyles.body(color: AppColors.inkSub));
    }

    final layout = precomputedLayout ?? computeBracketSlotLayout(rounds);
    final isChampionshipRound = highlightFinalMatchup && rounds.last.matchups.length == 1;
    final (:maxSlot, :championshipExtraWidth, :championshipExtraHeight, :championshipEntryGap) =
        _layoutMetrics(layout, isChampionshipRound);
    final totalWidth = rounds.length * BracketDimensions.cardWidth +
        (rounds.length - 1) * BracketDimensions.roundGap +
        championshipExtraWidth +
        championshipEntryGap;
    final totalHeight = maxSlot * BracketDimensions.verticalUnit + BracketDimensions.cardHeight + championshipExtraHeight;
    final hasSkipConnection = layout.connections.any((c) => c.isSkip);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (hasSkipConnection) ...[
          Row(
            crossAxisAlignment: CrossAxisAlignment.center,
            children: [
              const CustomPaint(size: Size(28, 12), painter: _DashedLegendSwatchPainter(color: AppColors.violet)),
              const SizedBox(width: 8),
              Flexible(
                child: Text(
                  'Winner advances directly to a later round, skipping the Elimination Game',
                  style: AppTextStyles.microLabel(color: AppColors.inkSub),
                ),
              ),
            ],
          ),
          const SizedBox(height: 12),
        ],
        HorizontalScrollableBracket(
          width: totalWidth,
          height: totalHeight + BracketDimensions.headerHeight + (conferenceLabels.isEmpty ? 10 : 10 + BracketDimensions.headerHeight),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              SizedBox(
                height: BracketDimensions.headerHeight,
                child: Stack(
                  children: [
                    for (var r = 0; r < rounds.length; r++)
                      Positioned(
                        left: r * (BracketDimensions.cardWidth + BracketDimensions.roundGap),
                        width: BracketDimensions.cardWidth,
                        child: Text(
                          rounds[r].round.toUpperCase(),
                          style: AppTextStyles.microLabel(),
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                  ],
                ),
              ),
              SizedBox(height: conferenceLabels.isEmpty ? 10 : 10 + BracketDimensions.headerHeight),
              SizedBox(
                width: totalWidth,
                height: totalHeight,
                child: Stack(
                  // conferenceLabels can sit above the first card's top (a
                  // negative `top`); Stack clips by default, which would
                  // silently drop that label.
                  clipBehavior: Clip.none,
                  children: [
                    for (final label in conferenceLabels)
                      Positioned(
                        left: 0,
                        top: label.slot * BracketDimensions.verticalUnit - BracketDimensions.labelClearance,
                        width: BracketDimensions.cardWidth,
                        child: Text(
                          label.name.toUpperCase(),
                          style: AppTextStyles.microLabel(color: AppColors.cyan),
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                    Positioned.fill(
                      child: CustomPaint(
                        painter: _BracketConnectorPainter(
                          connections: layout.connections,
                          color: AppColors.inkSub,
                          skipColor: AppColors.violet,
                          cardWidth: BracketDimensions.cardWidth,
                          cardHeight: BracketDimensions.cardHeight,
                          roundGap: BracketDimensions.roundGap,
                          verticalUnit: BracketDimensions.verticalUnit,
                          championshipRound: isChampionshipRound ? rounds.length - 1 : null,
                          championshipShift: championshipExtraWidth,
                          championshipExtraGap: championshipEntryGap,
                        ),
                      ),
                    ),
                    ..._cards(layout, isChampionshipRound, championshipExtraWidth, championshipExtraHeight, championshipEntryGap),
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

import 'package:flutter/material.dart';

import '../../core/models/season_projection.dart';
import '../../core/theme/app_colors.dart';
import 'season_bracket_dimensions.dart';
import 'season_bracket_matchup_card.dart';

/// The Championship card's own gradient border + glow -- see
/// BracketDimensions.championshipScale's own doc comment for why this
/// slot specifically gets emphasis every other bracket card doesn't.
class ChampionshipCard extends StatelessWidget {
  const ChampionshipCard({super.key, required this.sport, required this.matchup, required this.teamNames});

  final String sport;
  final BracketMatchup matchup;
  final Map<String, BracketTeamName> teamNames;

  // Combined outer + inner Padding below, each side -- the actual width
  // BracketMatchupCard renders at is BracketDimensions.championshipCardWidth
  // minus twice this, not the full championshipCardWidth. Passing the
  // outer width as its own cardWidth would understate how much space its
  // FittedBox status line actually has, overflowing it -- only visible in
  // debug mode (the overflow's hazard-stripe rendering and console
  // warning are both debug-only).
  static const double _borderInset = 2 + 1.5;

  @override
  Widget build(BuildContext context) {
    // The outer box is unfilled -- only its boxShadow (the glow) and the
    // 2px gradient ring below it are visible. That ring is the "border":
    // BracketMatchupCard's own card is meant to fully cover everything
    // inside it with its normal (same-as-every-other-card) background,
    // leaving only this thin band showing the gradient underneath -- but
    // that background is itself a translucent (~5-13% white) overlay,
    // designed to sit on the page's own solid dark background, not
    // directly on a vivid opaque gradient. Without an opaque backing
    // layer between them, the gradient showed straight through the
    // barely-there overlay, so the whole card read as solid gradient
    // fill instead of a normal card with a thin gradient border.
    return DecoratedBox(
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(16),
        boxShadow: [BoxShadow(color: AppColors.cyan.withValues(alpha: 0.25), blurRadius: 24, spreadRadius: 2)],
      ),
      child: Padding(
        padding: const EdgeInsets.all(2),
        child: DecoratedBox(
          decoration: BoxDecoration(borderRadius: BorderRadius.circular(14), gradient: AppColors.brandMark),
          child: Padding(
            padding: const EdgeInsets.all(1.5),
            child: DecoratedBox(
              decoration: BoxDecoration(borderRadius: BorderRadius.circular(13), color: AppColors.bg),
              child: ClipRRect(
                borderRadius: BorderRadius.circular(13),
                child: BracketMatchupCard(
                  sport: sport,
                  matchup: matchup,
                  teamNames: teamNames,
                  cardWidth: BracketDimensions.championshipCardWidth - 2 * _borderInset,
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

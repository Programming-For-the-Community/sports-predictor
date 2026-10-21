import 'package:flutter/material.dart';

import '../../core/models/season_projection.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import 'season_bracket_legend.dart';
import 'season_bracket_tree.dart';
import 'season_first_four_section.dart';
import 'season_march_madness_grid.dart';
import 'season_round_odds_table.dart';

/// March Madness's own region-shaped layout -- 4 regions (not 2
/// conferences) each played down to a champion, those 4 champions meeting
/// at a Final Four (2 games), then a Championship. Generalizes
/// computeConferenceBracketLayout's single region-pair-to-final merge to
/// a 2-stage merge (4 regions -> 2 Final Four winners -> 1 champion).
class MarchMadnessSection extends StatelessWidget {
  const MarchMadnessSection({super.key, required this.sport, required this.bracket});

  final String sport;
  final MarchMadnessBracket bracket;

  @override
  Widget build(BuildContext context) {
    final regionOrder = bracket.regions.keys.toList()..sort();
    final canGrid = regionOrder.length == 4 && bracket.finalFour.length == 2 && bracket.championship != null;
    final odds = bracket.roundOdds;

    // The grid path below places First Four cards itself, right next to
    // the Round of 64 slot each one feeds -- only the independent-tree
    // fallback (no fixed grid geometry to place them in) needs its own
    // separate, unpositioned section.
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (odds != null) ...[
          BracketLegend(simulation: (series: false, simulations: odds.simulations ?? 0)),
          const SizedBox(height: 4),
        ],
        if (!canGrid) ...[
          if (bracket.firstFour.isNotEmpty) ...[
            FirstFourSection(sport: sport, matchups: bracket.firstFour, bracket: bracket),
            const SizedBox(height: 20),
          ],
          // Not the shape this grid layout assumes -- fall back to
          // independent per-region trees.
          for (final region in regionOrder) ...[
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Text(region.toUpperCase(), style: AppTextStyles.microLabel(color: AppColors.cyan)),
            ),
            BracketTree(sport: sport, rounds: bracket.regions[region]!.rounds, teamNames: bracket.teamNames),
            const SizedBox(height: 20),
          ],
        ] else
          MarchMadnessGrid(sport: sport, bracket: bracket, regionOrder: regionOrder),
        if (odds != null) ...[
          const SizedBox(height: 24),
          RoundOddsTables(sport: sport, odds: odds, teamNames: bracket.teamNames, groupOrder: regionOrder),
        ],
      ],
    );
  }
}

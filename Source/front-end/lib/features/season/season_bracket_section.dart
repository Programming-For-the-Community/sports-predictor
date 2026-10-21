import 'package:flutter/material.dart';

import '../../core/models/season_projection.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import 'season_bracket_layout.dart';
import 'season_bracket_legend.dart';
import 'season_bracket_round_labels.dart';
import 'season_bracket_tree.dart';
import 'season_round_odds_table.dart';

/// Playoff/Cup-knockout bracket, rendered as a single converging tree
/// (BracketTree). A flat-bracket sport (NCAAFB -- bracket.rounds
/// non-null) already has its championship as the last round. A
/// conference-split sport (NFL/NBA -- bracket.conferences non-empty) has
/// two conferences that converge into one shared championship card;
/// computeConferenceBracketLayout lays each conference's tree out
/// independently and stacks them so their slot layouts can never collide.
class BracketSection extends StatelessWidget {
  const BracketSection({super.key, required this.sport, required this.bracket});

  final String sport;
  final BracketProjection bracket;

  @override
  Widget build(BuildContext context) {
    final odds = bracket.roundOdds;
    final conferenceNames = bracket.conferences.keys.toList()..sort();
    final tree = _tree(conferenceNames, _simulationLegend(odds));
    if (odds == null) return tree;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        tree,
        const SizedBox(height: 24),
        RoundOddsTables(sport: sport, odds: odds, teamNames: bracket.teamNames, groupOrder: conferenceNames),
      ],
    );
  }

  BracketSimulationLegend? _simulationLegend(RoundOdds? odds) {
    if (odds == null) return null;
    final matchups = [
      ...?bracket.rounds?.expand((round) => round.matchups),
      for (final rounds in bracket.conferences.values) ...rounds.expand((round) => round.matchups),
    ];
    return (series: matchups.any((m) => m.isSeries), simulations: odds.simulations ?? 0);
  }

  Widget _tree(List<String> conferenceNames, BracketSimulationLegend? legend) {
    final flatRounds = bracket.rounds;
    if (flatRounds != null) {
      return BracketTree(
        sport: sport, rounds: flatRounds, teamNames: bracket.teamNames, highlightFinalMatchup: true, simulationLegend: legend,
      );
    }

    final finalMatchup = bracket.finalMatchup;
    if (conferenceNames.length != 2 || finalMatchup == null) {
      // Not the shape this combined layout assumes -- fall back to
      // independent per-conference trees.
      return Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          for (final (i, conference) in conferenceNames.indexed) ...[
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Text(conference.toUpperCase(), style: AppTextStyles.microLabel(color: AppColors.cyan)),
            ),
            BracketTree(
              sport: sport,
              rounds: bracket.conferences[conference]!,
              teamNames: bracket.teamNames,
              simulationLegend: i == 0 ? legend : null,
            ),
            const SizedBox(height: 20),
          ],
        ],
      );
    }

    final roundsA = bracket.conferences[conferenceNames[0]]!;
    final roundsB = bracket.conferences[conferenceNames[1]]!;
    final roundCount = roundsA.length < roundsB.length ? roundsA.length : roundsB.length;

    final combinedRounds = [
      for (var r = 0; r < roundCount; r++)
        BracketRound(round: roundsA[r].round, matchups: [...roundsA[r].matchups, ...roundsB[r].matchups]),
      BracketRound(
        round: BracketRoundLabels.finalFor(sport),
        matchups: [finalMatchup],
      ),
    ];

    final combined = computeConferenceBracketLayout(roundsA, roundsB, finalMatchup);

    final conferenceLabels = [
      (name: conferenceNames[0], slot: 0.0),
      (name: conferenceNames[1], slot: combined.conferenceBOffset),
    ];

    return BracketTree(
      sport: sport,
      rounds: combinedRounds,
      teamNames: bracket.teamNames,
      conferenceLabels: conferenceLabels,
      precomputedLayout: combined.layout,
      highlightFinalMatchup: true,
      simulationLegend: legend,
    );
  }
}

import 'package:flutter/material.dart';

import '../../core/models/season_projection.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import 'season_bracket_dimensions.dart';
import 'season_bracket_matchup_card.dart';

/// Not drawn as a connector line into the region grid (_MarchMadnessGrid)
/// -- either there's no fixed grid geometry to place it in (the
/// independent-tree fallback, whole regions.length != 4 shape), or (a grid
/// IS present, but this particular game's own predicted winner couldn't be
/// matched into any region's Round of 64 -- shouldn't happen for a
/// self-consistent payload, see _MarchMadnessGrid's own
/// locateFirstFourDestination) -- either way this section is the fallback,
/// unpositioned rendering; each card names its resolved destination
/// directly since it isn't drawn.
class FirstFourSection extends StatelessWidget {
  const FirstFourSection({super.key, required this.sport, required this.matchups, required this.bracket});

  final String sport;
  final List<BracketMatchup> matchups;
  final MarchMadnessBracket bracket;

  /// The region whose Round of 64 the matchup's predicted winner already
  /// occupies -- null only if the region data doesn't contain that team
  /// (shouldn't happen for a self-consistent payload).
  String? _destinationRegion(BracketMatchup matchup) {
    final winner = matchup.predictedWinner;
    if (winner == null) return null;
    for (final entry in bracket.regions.entries) {
      final roundOfSixtyFour = entry.value.rounds.isEmpty ? null : entry.value.rounds.first;
      if (roundOfSixtyFour == null) continue;
      if (roundOfSixtyFour.matchups.any((m) => m.teamA == winner || m.teamB == winner)) return entry.key;
    }
    return null;
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('FIRST FOUR', style: AppTextStyles.microLabel()),
        const SizedBox(height: 8),
        Wrap(
          spacing: 12,
          runSpacing: 12,
          children: [
            for (final matchup in matchups)
              SizedBox(
                width: BracketDimensions.cardWidth,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    SizedBox(
                      height: BracketDimensions.cardHeight,
                      child: BracketMatchupCard(sport: sport, matchup: matchup, teamNames: bracket.teamNames, cardWidth: BracketDimensions.cardWidth),
                    ),
                    const SizedBox(height: 4),
                    Builder(builder: (context) {
                      final destination = _destinationRegion(matchup);
                      return Text(
                        destination == null ? 'Winner advances to Round of 64' : 'Winner → ${destination.toUpperCase()} • Round of 64',
                        style: AppTextStyles.microLabel(color: AppColors.inkSub),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      );
                    }),
                  ],
                ),
              ),
          ],
        ),
      ],
    );
  }
}

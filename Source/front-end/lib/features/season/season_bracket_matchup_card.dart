import 'package:flutter/material.dart';

import '../../core/models/season_projection.dart';
import '../../core/models/upset.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/pick_labels.dart';
import '../../core/widgets/team_color_dot.dart';
import '../../static/nfl_team_colors.dart';

const _maxTextScale = 1.1;

/// One bracket slot: each team's chance to win it, a series' wins (or a
/// finished game's score), and the UPSET / UPSET WATCH chip. Before the
/// result is in, the winner is the one in the shown simulated run.
class BracketMatchupCard extends StatelessWidget {
  const BracketMatchupCard({super.key, required this.sport, required this.matchup, required this.teamNames});

  final String sport;
  final BracketMatchup matchup;
  final Map<String, BracketTeamName> teamNames;

  @override
  Widget build(BuildContext context) {
    final winner = matchup.isFinal ? matchup.actualWinner : matchup.predictedWinner;
    final flag = matchup.flag;
    // The bracket lays cards out at a fixed cardHeight (BracketDimensions),
    // so unbounded system text scaling would push the rows past it.
    return MediaQuery.withClampedTextScaling(
      maxScaleFactor: _maxTextScale,
      child: Container(
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(12),
          gradient: const LinearGradient(begin: Alignment.topLeft, end: Alignment.bottomRight, colors: AppColors.surfaceGrad),
          border: Border.all(color: flag == UpsetFlag.upset ? AppColors.warn.withValues(alpha: 0.4) : AppColors.borderRaised),
        ),
        // Centred in the card's fixed height, chip or no chip.
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _BracketTeamRow(
              sport: sport,
              teamId: matchup.teamA,
              seed: matchup.seedA,
              teamNames: teamNames,
              isWinner: winner == matchup.teamA,
              chance: _chance(isA: true),
              score: _rowScore(isA: true),
            ),
            const SizedBox(height: 4),
            _BracketTeamRow(
              sport: sport,
              teamId: matchup.teamB,
              seed: matchup.seedB,
              teamNames: teamNames,
              isWinner: winner == matchup.teamB,
              chance: _chance(isA: false),
              score: _rowScore(isA: false),
            ),
            if (flag != null) ...[
              const SizedBox(height: 6),
              UpsetChip(flag: flag, dense: true),
            ],
          ],
        ),
      ),
    );
  }

  /// A finished slot shows the chance its pick had beforehand; any other
  /// shows the chance from where it stands now.
  double? _chance({required bool isA}) {
    if (matchup.teamA == null || matchup.teamB == null) return null;
    final now = isA ? matchup.chanceA : matchup.chanceB;
    if (!matchup.isFinal && now != null) return now;
    final probability = matchup.winProbability;
    final pick = matchup.predictedWinner;
    if (probability == null || pick == null) return null;
    final pickIsA = pick == matchup.teamA;
    return isA == pickIsA ? probability : 1 - probability;
  }

  /// A series' wins as the shown run ends it (the real record once final);
  /// a single game's score once final.
  int? _rowScore({required bool isA}) {
    if (matchup.isSeries) {
      return isA ? (matchup.seriesWinsA ?? matchup.winsA) : (matchup.seriesWinsB ?? matchup.winsB);
    }
    if (!matchup.isFinal) return null;
    return isA ? matchup.actualHomeScore : matchup.actualAwayScore;
  }
}

class _BracketTeamRow extends StatelessWidget {
  const _BracketTeamRow({
    required this.sport,
    required this.teamId,
    required this.seed,
    required this.teamNames,
    required this.isWinner,
    this.chance,
    this.score,
  });

  final String sport;
  // Null for the side that got a bye into this round -- see
  // BracketMatchup's own doc comment.
  final String? teamId;
  final int? seed;
  final Map<String, BracketTeamName> teamNames;
  final bool isWinner;
  final double? chance;
  final int? score;

  @override
  Widget build(BuildContext context) {
    final id = teamId;
    final seedCell = seed == null
        ? null
        : SizedBox(width: 20, child: Text('$seed', style: AppTextStyles.microLabel(color: AppColors.inkMute)));
    if (id == null) {
      return Row(
        children: [
          if (seedCell != null) seedCell,
          Expanded(
            child: Text('BYE', style: AppTextStyles.body(color: AppColors.inkMute), overflow: TextOverflow.ellipsis),
          ),
        ],
      );
    }
    final info = teamDisplayFor(sport, id, teamNames[id]?.abbreviation, apiColor: teamNames[id]?.color);
    final chance = this.chance;
    return Row(
      children: [
        if (seedCell != null) seedCell,
        TeamColorDot(color: info.primary),
        if (info.primary != null) const SizedBox(width: 8),
        Expanded(
          child: Text(
            info.abbreviation,
            style: AppTextStyles.body(color: isWinner ? AppColors.ink : AppColors.inkSub),
            overflow: TextOverflow.ellipsis,
          ),
        ),
        if (chance != null)
          Text(formatBracketChance(chance), style: AppTextStyles.microLabel(color: isWinner ? AppColors.cyan : AppColors.inkSub)),
        if (score != null)
          Container(
            constraints: const BoxConstraints(minWidth: 28),
            alignment: Alignment.centerRight,
            child: Text(
              '$score',
              textAlign: TextAlign.right,
              style: AppTextStyles.metricValue(color: isWinner ? AppColors.ink : AppColors.inkSub),
            ),
          ),
      ],
    );
  }
}

/// A whole percent, "<1%" for a sliver, ">99%" for all but certain.
String formatBracketChance(double chance) {
  if (chance > 0 && chance < 0.005) return '<1%';
  if (chance < 1 && chance > 0.995) return '>99%';
  return '${(chance * 100).round()}%';
}

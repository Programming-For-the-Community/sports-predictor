import 'package:flutter/material.dart';

import '../../core/models/season_projection.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/team_color_dot.dart';
import '../../static/nfl_team_colors.dart';

/// Winner-first predicted series record (e.g. "4-2"), or null if either
/// side's predicted win count is missing.
String? _seriesPredictedRecord(BracketMatchup matchup) {
  if (matchup.predictedWinsA == null || matchup.predictedWinsB == null) return null;
  if (matchup.predictedWinner == matchup.teamA) return '${matchup.predictedWinsA}-${matchup.predictedWinsB}';
  return '${matchup.predictedWinsB}-${matchup.predictedWinsA}';
}

class BracketMatchupCard extends StatelessWidget {
  const BracketMatchupCard({super.key, required this.sport, required this.matchup, required this.teamNames, required this.cardWidth});

  final String sport;
  final BracketMatchup matchup;
  final Map<String, BracketTeamName> teamNames;

  /// Threaded from _BracketTree's own card width -- gives the status line a
  /// real width to wrap against inside the FittedBox below.
  final double cardWidth;

  @override
  Widget build(BuildContext context) {
    final winner = matchup.isFinal ? matchup.actualWinner : matchup.predictedWinner;
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(12),
        gradient: const LinearGradient(begin: Alignment.topLeft, end: Alignment.bottomRight, colors: AppColors.surfaceGrad),
        border: Border.all(color: AppColors.borderRaised),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _BracketTeamRow(
            sport: sport,
            teamId: matchup.teamA,
            seed: matchup.seedA,
            teamNames: teamNames,
            isWinner: winner == matchup.teamA,
            // A series' running win count takes priority over a single
            // game's score, shown throughout (including 0-0).
            score: matchup.isSeries ? matchup.winsA : (matchup.isFinal ? matchup.actualHomeScore : null),
          ),
          const SizedBox(height: 4),
          _BracketTeamRow(
            sport: sport,
            teamId: matchup.teamB,
            seed: matchup.seedB,
            teamNames: teamNames,
            isWinner: winner == matchup.teamB,
            score: matchup.isSeries ? matchup.winsB : (matchup.isFinal ? matchup.actualAwayScore : null),
          ),
          const SizedBox(height: 6),
          // Shrinks the wrapped status text to fit whatever vertical space
          // remains, so it can't overflow the fixed-height card.
          Expanded(
            child: FittedBox(
              fit: BoxFit.scaleDown,
              alignment: Alignment.topLeft,
              child: SizedBox(
                width: cardWidth - 24, // Container's own 12px padding each side
                child: Text(
                  _statusLabel(),
                  style: AppTextStyles.microLabel(color: _statusColor()),
                  maxLines: 3,
                  softWrap: true,
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }

  String _teamLabel(String teamId) {
    final info = teamNames[teamId];
    return teamDisplayFor(sport, teamId, info?.abbreviation).abbreviation;
  }

  // "Projected"/"Scheduled"/"Final". A series matchup (isSeries) folds in
  // its running win-loss record.
  String _statusLabel() => matchup.isSeries ? _seriesStatusLabel() : _singleGameStatusLabel();

  // The live win-loss record (winsA/winsB) is shown per-team by this
  // card's own team rows, so it isn't repeated here. Only the predicted
  // final record (winner-first, e.g. "4-2") appears on the status line.
  String _seriesStatusLabel() {
    final winnerLabel = matchup.predictedWinner != null ? _teamLabel(matchup.predictedWinner!) : null;
    final predictedRecord = _seriesPredictedRecord(matchup);
    switch (matchup.status) {
      case BracketMatchupStatus.finalStatus:
        return _seriesFinalLabel();
      case BracketMatchupStatus.scheduled:
        return _seriesScheduledLabel(winnerLabel, predictedRecord);
      default:
        return _seriesProjectedLabel(winnerLabel, predictedRecord);
    }
  }

  String _seriesFinalLabel() {
    final winnerName = matchup.actualWinner != null ? _teamLabel(matchup.actualWinner!) : null;
    return winnerName != null ? '$winnerName WINS SERIES' : 'SERIES FINAL';
  }

  String _seriesScheduledLabel(String? winnerLabel, String? predictedRecord) {
    if (matchup.winProbability == null || winnerLabel == null) return 'PREDICTION PENDING';
    final probability = '${(matchup.winProbability! * 100).round()}%';
    return predictedRecord != null ? '$winnerLabel $predictedRecord $probability' : '$probability $winnerLabel';
  }

  String _seriesProjectedLabel(String? winnerLabel, String? predictedRecord) {
    if (matchup.winProbability == null || winnerLabel == null) return 'PROJECTED';
    final probability = '${(matchup.winProbability! * 100).round()}%';
    return predictedRecord != null
        ? 'PROJECTED — $winnerLabel $predictedRecord $probability'
        : 'PROJECTED — $probability $winnerLabel';
  }

  String _singleGameStatusLabel() {
    final winnerLabel = matchup.predictedWinner != null ? _teamLabel(matchup.predictedWinner!) : null;
    switch (matchup.status) {
      case BracketMatchupStatus.finalStatus:
        return 'FINAL';
      case BracketMatchupStatus.scheduled:
        return matchup.winProbability != null && winnerLabel != null
            ? '${(matchup.winProbability! * 100).round()}% $winnerLabel'
            : 'PREDICTION PENDING';
      default:
        return matchup.winProbability != null && winnerLabel != null
            ? 'PROJECTED — ${(matchup.winProbability! * 100).round()}% $winnerLabel'
            : 'PROJECTED';
    }
  }

  Color _statusColor() {
    switch (matchup.status) {
      case BracketMatchupStatus.finalStatus:
        return AppColors.cyan;
      case BracketMatchupStatus.scheduled:
        return AppColors.ink;
      default:
        return AppColors.inkMute;
    }
  }
}

class _BracketTeamRow extends StatelessWidget {
  const _BracketTeamRow({
    required this.sport,
    required this.teamId,
    required this.seed,
    required this.teamNames,
    required this.isWinner,
    this.score,
  });

  final String sport;
  // Null for the side that got a bye into this round -- see
  // BracketMatchup's own doc comment.
  final String? teamId;
  final int? seed;
  final Map<String, BracketTeamName> teamNames;
  final bool isWinner;
  final int? score;

  @override
  Widget build(BuildContext context) {
    final id = teamId;
    if (id == null) {
      return Row(
        children: [
          if (seed != null)
            SizedBox(width: 20, child: Text('$seed', style: AppTextStyles.microLabel(color: AppColors.inkMute))),
          Expanded(
            child: Text('BYE', style: AppTextStyles.body(color: AppColors.inkMute), overflow: TextOverflow.ellipsis),
          ),
        ],
      );
    }
    final info = teamDisplayFor(sport, id, teamNames[id]?.abbreviation, apiColor: teamNames[id]?.color);
    return Row(
      children: [
        if (seed != null)
          SizedBox(width: 20, child: Text('$seed', style: AppTextStyles.microLabel(color: AppColors.inkMute))),
        TeamColorDot(color: info.primary),
        if (info.primary != null) const SizedBox(width: 8),
        Expanded(
          child: Text(
            info.abbreviation,
            style: AppTextStyles.body(color: isWinner ? AppColors.ink : AppColors.inkSub),
            overflow: TextOverflow.ellipsis,
          ),
        ),
        if (score != null)
          Text('$score', style: AppTextStyles.metricValue(color: isWinner ? AppColors.cyan : AppColors.inkMute)),
      ],
    );
  }
}

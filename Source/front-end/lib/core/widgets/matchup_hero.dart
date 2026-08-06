import 'package:flutter/material.dart';

import '../models/event.dart';
import '../models/prediction.dart';
import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';
import '../../static/nfl_team_colors.dart';
import 'confidence_pill.dart';
import 'win_probability_bar.dart';

/// design/FRONTEND_STYLE.md's "Matchup hero (detail)" component: two
/// columns of team + big percentage (favored side gradient-clipped cyan),
/// a split bar, then a Pick / Pred margin / Pred total stat trio.
class MatchupHero extends StatelessWidget {
  const MatchupHero({super.key, required this.event, required this.prediction});

  final SportEvent event;
  final EventPrediction prediction;

  @override
  Widget build(BuildContext context) {
    final home = nflTeam(event.home.entityId);
    final away = nflTeam(event.away.entityId);
    final homeFavored = prediction.homeWinProbability >= 0.5;

    return Container(
      padding: const EdgeInsets.all(28),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(20),
        gradient: LinearGradient(begin: Alignment.topLeft, end: Alignment.bottomRight, colors: AppColors.surfaceGrad),
        border: Border.all(color: AppColors.borderRaised),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              Expanded(
                child: _TeamColumn(
                  color: away.primary,
                  abbr: away.abbreviation,
                  probability: 1 - prediction.homeWinProbability,
                  favored: !homeFavored,
                  predictedScore: prediction.awayScore,
                ),
              ),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16),
                // "@" reads as "away @ home" in American sports (the away
                // team is the one traveling) -- away is the LEFT column
                // above specifically so this ordering matches that. This
                // is the only marker of which side is home/away -- no
                // separate "HOME"/"AWAY" label, same convention as
                // game_row.dart's _MatchupLine.
                child: Text('@', style: AppTextStyles.sectionTitle(color: AppColors.inkMute)),
              ),
              Expanded(
                child: _TeamColumn(
                  color: home.primary,
                  abbr: home.abbreviation,
                  probability: prediction.homeWinProbability,
                  favored: homeFavored,
                  predictedScore: prediction.homeScore,
                ),
              ),
            ],
          ),
          const SizedBox(height: 20),
          WinProbabilityBar(homeWinProbability: prediction.homeWinProbability, height: 12),
          const SizedBox(height: 12),
          Center(child: ConfidencePill(homeWinProbability: prediction.homeWinProbability)),
          const SizedBox(height: 24),
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceAround,
            children: [
              _StatTrio(label: 'PICK', value: homeFavored ? home.abbreviation : away.abbreviation),
              _StatTrio(label: 'PRED MARGIN', value: prediction.margin.toStringAsFixed(1)),
              _StatTrio(
                label: 'PRED TOTAL',
                value: (prediction.homeScore + prediction.awayScore).toStringAsFixed(1),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _TeamColumn extends StatelessWidget {
  const _TeamColumn({
    required this.color, required this.abbr, required this.probability, required this.favored,
    required this.predictedScore,
  });
  final Color color;
  final String abbr;
  final double probability;
  final bool favored;
  final double predictedScore;

  @override
  Widget build(BuildContext context) {
    final numeral = Text('${(probability * 100).round()}%', style: AppTextStyles.bigStatNumeral());

    return Column(
      children: [
        Container(width: 10, height: 10, decoration: BoxDecoration(shape: BoxShape.circle, color: color)),
        const SizedBox(height: 8),
        Text(abbr, style: AppTextStyles.cardTitle()),
        const SizedBox(height: 8),
        favored
            ? ShaderMask(
                shaderCallback: (bounds) => AppColors.cyanFill.createShader(bounds),
                blendMode: BlendMode.srcIn,
                child: numeral,
              )
            : Opacity(opacity: 0.6, child: numeral),
        const SizedBox(height: 4),
        // Each team's own predicted score -- PRED TOTAL below is the
        // combined over/under figure, a different stat from this.
        Text('${predictedScore.round()} PTS', style: AppTextStyles.microLabel(color: AppColors.inkMute)),
      ],
    );
  }
}

/// Completed-game counterpart to MatchupHero -- same card shape/team-color
/// language, but shows the FINAL score (not a live probability, which
/// would be misleading for a game that's already over -- see
/// event_detail_page.dart's own comment) plus the prediction actually
/// logged before the game was played, via `comparison` (event.dart's
/// PredictionComparison, already fetched by the events list -- no live
/// /predictions/events/{id} call for a completed game). `comparison` is
/// null when no prediction was ever logged for this event before it was
/// played (see PredictionComparison's own docs), not a loading state.
class MatchupResultHero extends StatelessWidget {
  const MatchupResultHero({super.key, required this.event, required this.comparison});

  final SportEvent event;
  final PredictionComparison? comparison;

  @override
  Widget build(BuildContext context) {
    final home = nflTeam(event.home.entityId);
    final away = nflTeam(event.away.entityId);
    final homeWon = event.home.result?.won ?? false;

    return Container(
      padding: const EdgeInsets.all(28),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(20),
        gradient: LinearGradient(begin: Alignment.topLeft, end: Alignment.bottomRight, colors: AppColors.surfaceGrad),
        border: Border.all(color: AppColors.borderRaised),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              Expanded(
                child: _ResultTeamColumn(
                  color: away.primary, abbr: away.abbreviation,
                  score: event.away.result?.score, won: !homeWon,
                ),
              ),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16),
                // Away-left/home-right + "@" -- same convention as
                // MatchupHero and game_row.dart's _MatchupLine, "FINAL"
                // underneath just adds the completed-game status on top.
                child: Column(
                  children: [
                    Text('@', style: AppTextStyles.sectionTitle(color: AppColors.inkMute)),
                    Text('FINAL', style: AppTextStyles.microLabel(color: AppColors.inkMute)),
                  ],
                ),
              ),
              Expanded(
                child: _ResultTeamColumn(
                  color: home.primary, abbr: home.abbreviation,
                  score: event.home.result?.score, won: homeWon,
                ),
              ),
            ],
          ),
          const SizedBox(height: 24),
          _PredictionRecap(comparison: comparison),
        ],
      ),
    );
  }
}

class _ResultTeamColumn extends StatelessWidget {
  const _ResultTeamColumn({required this.color, required this.abbr, required this.score, required this.won});
  final Color color;
  final String abbr;
  final double? score;
  final bool won;

  @override
  Widget build(BuildContext context) {
    final numeral = Text(score?.toStringAsFixed(0) ?? '--', style: AppTextStyles.bigStatNumeral());

    return Column(
      children: [
        Container(width: 10, height: 10, decoration: BoxDecoration(shape: BoxShape.circle, color: color)),
        const SizedBox(height: 8),
        Text(abbr, style: AppTextStyles.cardTitle()),
        const SizedBox(height: 8),
        won
            ? ShaderMask(
                shaderCallback: (bounds) => AppColors.cyanFill.createShader(bounds),
                blendMode: BlendMode.srcIn,
                child: numeral,
              )
            : Opacity(opacity: 0.6, child: numeral),
      ],
    );
  }
}

class _PredictionRecap extends StatelessWidget {
  const _PredictionRecap({required this.comparison});
  final PredictionComparison? comparison;

  @override
  Widget build(BuildContext context) {
    final c = comparison;
    if (c == null) {
      return Center(
        child: Text('No prediction was recorded for this game.', style: AppTextStyles.body(color: AppColors.inkMute)),
      );
    }
    return Column(
      children: [
        Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(c.correct ? Icons.check_circle : Icons.cancel, color: c.correct ? AppColors.live : AppColors.neg, size: 18),
            const SizedBox(width: 8),
            Text(
              c.correct ? 'Model picked the winner' : 'Model missed the winner',
              style: AppTextStyles.body(color: AppColors.inkSub),
            ),
          ],
        ),
        const SizedBox(height: 16),
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceAround,
          children: [
            _StatTrio(label: 'PREDICTED', value: '${(c.predictedHomeWinProbability * 100).round()}% HOME'),
            if (c.predictedMargin != null)
              _StatTrio(label: 'PRED MARGIN', value: c.predictedMargin!.toStringAsFixed(1)),
            _StatTrio(label: 'ACTUAL MARGIN', value: c.actualMargin.toStringAsFixed(1)),
          ],
        ),
      ],
    );
  }
}

class _StatTrio extends StatelessWidget {
  const _StatTrio({required this.label, required this.value});
  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Text(label, style: AppTextStyles.microLabel()),
        const SizedBox(height: 4),
        Text(value, style: AppTextStyles.metricValueLarge()),
      ],
    );
  }
}

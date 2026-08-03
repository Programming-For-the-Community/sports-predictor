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
                  color: home.primary,
                  abbr: home.abbreviation,
                  probability: prediction.homeWinProbability,
                  favored: homeFavored,
                ),
              ),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16),
                child: Text('@', style: AppTextStyles.sectionTitle(color: AppColors.inkMute)),
              ),
              Expanded(
                child: _TeamColumn(
                  color: away.primary,
                  abbr: away.abbreviation,
                  probability: 1 - prediction.homeWinProbability,
                  favored: !homeFavored,
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
  const _TeamColumn({required this.color, required this.abbr, required this.probability, required this.favored});
  final Color color;
  final String abbr;
  final double probability;
  final bool favored;

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

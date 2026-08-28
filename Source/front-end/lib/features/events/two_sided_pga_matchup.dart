import 'package:flutter/material.dart';

import '../../core/models/field_prediction.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/win_probability_bar.dart';

/// Renders a TwoSidedPgaPrediction (match_play or cup) -- a compact
/// home/away matchup view, the same head-to-head framing a real sport
/// uses since a Ryder/Presidents Cup match or a whole Cup result
/// genuinely is 2-sided (unlike a field event). WinProbabilityBar is
/// reused as-is -- it's already sport-agnostic, taking a bare probability
/// double with no head-to-head-specific coupling.
class TwoSidedPgaMatchup extends StatelessWidget {
  const TwoSidedPgaMatchup({super.key, required this.prediction});

  final TwoSidedPgaPrediction prediction;

  @override
  Widget build(BuildContext context) {
    final home = prediction.home;
    final away = prediction.away;
    final winProbability = prediction.winProbability?.value;

    return Container(
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(16),
        gradient: LinearGradient(begin: Alignment.topLeft, end: Alignment.bottomRight, colors: AppColors.surfaceGrad),
        border: Border.all(color: AppColors.borderRaised),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (prediction.tournamentName != null)
            Text(prediction.tournamentName!, style: AppTextStyles.sectionTitle(color: AppColors.violet)),
          if (prediction.sessionName != null) ...[
            const SizedBox(height: 4),
            Text(prediction.sessionName!, style: AppTextStyles.microLabel(color: AppColors.inkMute)),
          ],
          const SizedBox(height: 16),
          _SideLine(side: away, isHome: false),
          const SizedBox(height: 8),
          _SideLine(side: home, isHome: true),
          const SizedBox(height: 16),
          if (winProbability != null) ...[
            WinProbabilityBar(homeWinProbability: winProbability),
            const SizedBox(height: 8),
            Text(
              '${home?.name ?? 'Home'} ${(winProbability * 100).round()}% -- '
              '${away?.name ?? 'Away'} ${((1 - winProbability) * 100).round()}%',
              style: AppTextStyles.microLabel(color: AppColors.inkMute),
            ),
          ] else
            Text('No prediction available yet.', style: AppTextStyles.body(color: AppColors.inkMute)),
          if (prediction.status == 'completed' && prediction.actualHomeWon != null) ...[
            const SizedBox(height: 16),
            const Divider(height: 1, color: AppColors.border),
            const SizedBox(height: 12),
            _ActualResultLine(home: home, away: away, homeWon: prediction.actualHomeWon!, halved: prediction.actualHalved ?? false),
          ],
        ],
      ),
    );
  }
}

class _SideLine extends StatelessWidget {
  const _SideLine({required this.side, required this.isHome});

  final MatchPlaySide? side;
  final bool isHome;

  @override
  Widget build(BuildContext context) {
    final name = side?.name ?? (isHome ? 'Home' : 'Away');
    final golfers = side?.golfers;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(name, style: AppTextStyles.body(color: AppColors.ink)),
        if (golfers != null && golfers.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 2),
            child: Text(
              golfers.map((g) => g.name ?? g.entityId).join(' / '),
              style: AppTextStyles.microLabel(color: AppColors.inkSub),
            ),
          ),
      ],
    );
  }
}

class _ActualResultLine extends StatelessWidget {
  const _ActualResultLine({required this.home, required this.away, required this.homeWon, required this.halved});

  final MatchPlaySide? home;
  final MatchPlaySide? away;
  final bool homeWon;
  final bool halved;

  @override
  Widget build(BuildContext context) {
    final label = halved
        ? 'Match halved'
        : '${homeWon ? (home?.name ?? 'Home') : (away?.name ?? 'Away')} won';
    return Row(
      children: [
        Icon(halved ? Icons.remove_circle_outline : Icons.check_circle, color: halved ? AppColors.inkMute : AppColors.live, size: 18),
        const SizedBox(width: 8),
        Text(label, style: AppTextStyles.body(color: AppColors.inkSub)),
      ],
    );
  }
}

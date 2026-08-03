import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../data/events_repository.dart';
import '../models/event.dart';
import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';
import '../../static/nfl_team_colors.dart';
import 'confidence_pill.dart';
import 'win_probability_bar.dart';

/// design/FRONTEND_STYLE.md's "Game row (list)" component. Fetches its own
/// prediction (one request per visible row) rather than the list page
/// batching them -- NFL's weekly game count (~16) makes this a non-issue,
/// and it means a slow/failed prediction for one game never blocks the
/// rest of the list from rendering.
class GameRow extends ConsumerWidget {
  const GameRow({super.key, required this.sport, required this.event});

  final String sport;
  final SportEvent event;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final home = nflTeam(event.home.entityId);
    final away = nflTeam(event.away.entityId);
    final prediction = ref.watch(eventPredictionProvider((sport: sport, eventId: event.eventId)));

    return InkWell(
      onTap: () => context.go('/$sport/events/${event.eventId}'),
      borderRadius: BorderRadius.circular(16),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 16),
        decoration: BoxDecoration(
          color: AppColors.surface,
          borderRadius: BorderRadius.circular(16),
          border: Border.all(color: AppColors.border),
        ),
        child: Row(
          children: [
            SizedBox(
              width: 56,
              child: Text(
                event.week != null ? 'WK ${event.week}' : '',
                style: AppTextStyles.microLabel(),
              ),
            ),
            Expanded(
              flex: 2,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  _TeamLine(color: home.primary, abbr: home.abbreviation),
                  const SizedBox(height: 4),
                  _TeamLine(color: away.primary, abbr: away.abbreviation),
                ],
              ),
            ),
            const SizedBox(width: 16),
            Expanded(
              flex: 3,
              child: prediction.when(
                data: (p) => Row(
                  children: [
                    Expanded(child: WinProbabilityBar(homeWinProbability: p.homeWinProbability)),
                    const SizedBox(width: 12),
                    Text(
                      '${(p.homeWinProbability * 100).round()}%',
                      style: AppTextStyles.metricValueLarge(color: AppColors.cyan),
                    ),
                    const SizedBox(width: 8),
                    ConfidencePill(homeWinProbability: p.homeWinProbability),
                  ],
                ),
                loading: () => const WinProbabilityBar(homeWinProbability: 0.5),
                error: (_, __) => Text('--', style: AppTextStyles.body(color: AppColors.inkMute)),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _TeamLine extends StatelessWidget {
  const _TeamLine({required this.color, required this.abbr});
  final Color color;
  final String abbr;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(width: 8, height: 8, decoration: BoxDecoration(shape: BoxShape.circle, color: color)),
        const SizedBox(width: 8),
        Text(abbr, style: AppTextStyles.body(color: AppColors.ink)),
      ],
    );
  }
}

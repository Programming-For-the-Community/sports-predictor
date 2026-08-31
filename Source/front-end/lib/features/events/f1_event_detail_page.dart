import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/data/f1_events_repository.dart';
import '../../core/models/f1_prediction.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/f1_prediction_computing_retry.dart';
import '../../core/widgets/f1_prediction_freshness_badge.dart';
import 'f1_leaderboard_table.dart';

// No live-scores poll yet (F1's own live-scores Lambda is still deferred,
// see project-f1-onboarding memory) -- this only re-fetches the cached
// prediction itself, same interval as event_detail_page.dart's own,
// which is enough to pick up a stale->fresh transition after the predict
// Lambda finishes a background recompute.
const _pollInterval = Duration(seconds: 30);

/// F1's own detail page -- mirrors field_event_detail_page.dart's
/// Timer.periodic polling shell, but with no event_type branch at the
/// PAGE level (unlike PGA's field/match_play/cup split): both F1
/// event_types (field/sprint) render through the SAME leaderboard-table
/// shape, just with a different column set (F1LeaderboardTable's own
/// isSprint flag) and "field" alone additionally shows a constructors
/// panel.
class F1EventDetailPage extends ConsumerStatefulWidget {
  const F1EventDetailPage({super.key, required this.sportId, required this.eventId});

  final String sportId;
  final String eventId;

  @override
  ConsumerState<F1EventDetailPage> createState() => _F1EventDetailPageState();
}

class _F1EventDetailPageState extends ConsumerState<F1EventDetailPage> {
  Timer? _pollTimer;

  @override
  void initState() {
    super.initState();
    _pollTimer = Timer.periodic(_pollInterval, (_) => _poll());
  }

  @override
  void dispose() {
    _pollTimer?.cancel();
    super.dispose();
  }

  void _poll() {
    ref.invalidate(f1EventPredictionProvider((sport: widget.sportId, eventId: widget.eventId)));
  }

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      padding: const EdgeInsets.all(24),
      child: ref.watch(f1EventPredictionProvider((sport: widget.sportId, eventId: widget.eventId))).when(
            data: (prediction) => _PredictionView(sport: widget.sportId, eventId: widget.eventId, prediction: prediction),
            loading: () => const Center(child: CircularProgressIndicator()),
            error: (error, _) => error is PredictionComputingException
                ? F1PredictionComputingRetry(sport: widget.sportId, eventId: widget.eventId, retryAfterSeconds: error.retryAfterSeconds)
                : Text('Couldn\'t load prediction: $error', style: AppTextStyles.body(color: AppColors.neg)),
          ),
    );
  }
}

class _PredictionView extends StatelessWidget {
  const _PredictionView({required this.sport, required this.eventId, required this.prediction});

  final String sport;
  final String eventId;
  final F1EventPrediction prediction;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          children: [
            Expanded(
              child: Text(
                prediction.raceName ?? (prediction.isSprint ? 'Sprint' : 'Grand Prix'),
                style: AppTextStyles.sectionTitle(color: AppColors.violet),
              ),
            ),
            if (prediction.isSprint)
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                decoration: BoxDecoration(color: AppColors.cyan.withValues(alpha: 0.15), borderRadius: BorderRadius.circular(999)),
                child: Text('SPRINT', style: AppTextStyles.microLabel(color: AppColors.cyan)),
              ),
          ],
        ),
        if (prediction.stale) ...[
          const SizedBox(height: 12),
          F1PredictionFreshnessBadge(
            sport: sport, eventId: eventId, stale: prediction.stale, retryAfterSeconds: prediction.staleRetryAfterSeconds,
          ),
        ],
        const SizedBox(height: 16),
        F1LeaderboardTable(field: prediction.field, isSprint: prediction.isSprint),
        if (prediction.constructors.isNotEmpty) ...[
          const SizedBox(height: 24),
          Text('CONSTRUCTORS', style: AppTextStyles.microLabel()),
          const SizedBox(height: 8),
          _ConstructorsTable(constructors: prediction.constructors),
        ],
      ],
    );
  }
}

class _ConstructorsTable extends StatelessWidget {
  const _ConstructorsTable({required this.constructors});
  final List<F1ConstructorPrediction> constructors;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 20),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(16),
        gradient: LinearGradient(begin: Alignment.topLeft, end: Alignment.bottomRight, colors: AppColors.surfaceGrad),
        border: Border.all(color: AppColors.borderRaised),
      ),
      child: Column(
        children: [
          for (var i = 0; i < constructors.length; i++) ...[
            if (i > 0) const Divider(height: 1, color: AppColors.border),
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 12),
              child: Row(
                children: [
                  Text('${i + 1}', style: AppTextStyles.metricValue(color: AppColors.inkMute)),
                  const SizedBox(width: 16),
                  Expanded(
                    child: Text(
                      constructors[i].name ?? constructors[i].entityId,
                      style: AppTextStyles.body(color: AppColors.ink),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                  Text(
                    constructors[i].winProbability != null ? '${(constructors[i].winProbability!.value * 100).round()}% WIN' : '--',
                    style: AppTextStyles.metricValue(color: AppColors.violet),
                  ),
                ],
              ),
            ),
          ],
        ],
      ),
    );
  }
}

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/data/field_events_repository.dart';
import '../../core/data/live_scores_repository.dart';
import '../../core/models/field_live_score.dart';
import '../../core/models/field_prediction.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/field_prediction_computing_retry.dart';
import '../../core/widgets/field_prediction_freshness_badge.dart';
import 'field_leaderboard_table.dart';
import 'two_sided_pga_matchup.dart';

// Proportional to the backend's own 5-minute cache refresh
// (pga-live-scores' scheduler) -- over-polling a cheap cached GET is
// harmless, but 30s (event_detail_page.dart's own interval) buys nothing
// extra given the server-side cadence.
const _pollInterval = Duration(seconds: 60);

/// PGA's own detail page -- mirrors event_detail_page.dart's Timer.
/// periodic polling shell, but branches on the resolved
/// PgaEventPrediction's runtime type: FieldEventPrediction gets a
/// leaderboard table, TwoSidedPgaPrediction (match_play/cup) gets a
/// compact matchup view. This is the ONLY place PGA's three event_types
/// diverge in the frontend -- the list page (field_event_list_page.dart)
/// deliberately shows all three uniformly, see field_event_row.dart's own
/// doc comment for why.
class FieldEventDetailPage extends ConsumerStatefulWidget {
  const FieldEventDetailPage({super.key, required this.sportId, required this.eventId});

  final String sportId;
  final String eventId;

  @override
  ConsumerState<FieldEventDetailPage> createState() => _FieldEventDetailPageState();
}

class _FieldEventDetailPageState extends ConsumerState<FieldEventDetailPage> {
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
    ref.invalidate(fieldLiveScoresProvider(widget.sportId));
    ref.invalidate(pgaLiveScoresProvider(widget.sportId));
    ref.invalidate(fieldEventPredictionProvider((sport: widget.sportId, eventId: widget.eventId)));
  }

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      padding: const EdgeInsets.all(24),
      child: ref.watch(fieldEventPredictionProvider((sport: widget.sportId, eventId: widget.eventId))).when(
            data: (prediction) => switch (prediction) {
              PgaFieldPrediction() => _FieldPredictionView(sport: widget.sportId, eventId: widget.eventId, prediction: prediction.prediction),
              PgaTwoSidedPrediction() => _TwoSidedPredictionView(sport: widget.sportId, eventId: widget.eventId, prediction: prediction.prediction),
            },
            loading: () => const Center(child: CircularProgressIndicator()),
            error: (error, _) => error is PredictionComputingException
                ? FieldPredictionComputingRetry(sport: widget.sportId, eventId: widget.eventId, retryAfterSeconds: error.retryAfterSeconds)
                : Text('Couldn\'t load prediction: $error', style: AppTextStyles.body(color: AppColors.neg)),
          ),
    );
  }
}

class _FieldPredictionView extends ConsumerWidget {
  const _FieldPredictionView({required this.sport, required this.eventId, required this.prediction});

  final String sport;
  final String eventId;
  final FieldEventPrediction prediction;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final liveScores = ref.watch(fieldLiveScoresProvider(sport)).value ?? const <String, FieldLiveEventState>{};
    final liveState = liveScores[eventId];
    final cutline = prediction.cutline?.projectedCutScore?.value;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (prediction.tournamentName != null)
          Text(prediction.tournamentName!, style: AppTextStyles.sectionTitle(color: AppColors.violet)),
        if (cutline != null) ...[
          const SizedBox(height: 4),
          Text(
            'Projected cutline: ${cutline > 0 ? '+' : ''}${cutline.round()}',
            style: AppTextStyles.microLabel(color: AppColors.inkMute),
          ),
        ],
        if (prediction.stale) ...[
          const SizedBox(height: 12),
          FieldPredictionFreshnessBadge(
            sport: sport, eventId: eventId, stale: prediction.stale, retryAfterSeconds: prediction.staleRetryAfterSeconds,
          ),
        ],
        const SizedBox(height: 16),
        FieldLeaderboardTable(field: prediction.field, liveResults: liveState?.participants ?? const {}),
      ],
    );
  }
}

class _TwoSidedPredictionView extends ConsumerWidget {
  const _TwoSidedPredictionView({required this.sport, required this.eventId, required this.prediction});

  final String sport;
  final String eventId;
  final TwoSidedPgaPrediction prediction;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final liveScores = ref.watch(pgaLiveScoresProvider(sport)).value ?? const <String, PgaLiveEventState>{};
    final liveState = switch (liveScores[eventId]) {
      PgaTwoSidedLiveState(:final state) => state,
      _ => null,
    };

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        TwoSidedPgaMatchup(prediction: prediction, liveState: liveState),
        if (prediction.stale) ...[
          const SizedBox(height: 12),
          Center(
            child: FieldPredictionFreshnessBadge(
              sport: sport, eventId: eventId, stale: prediction.stale, retryAfterSeconds: prediction.staleRetryAfterSeconds,
            ),
          ),
        ],
      ],
    );
  }
}

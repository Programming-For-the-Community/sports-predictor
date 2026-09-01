import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/data/f1_events_repository.dart';
import '../../core/data/live_scores_repository.dart';
import '../../core/models/f1_live_score.dart';
import '../../core/models/f1_prediction.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/f1_prediction_computing_retry.dart';
import '../../core/widgets/f1_prediction_freshness_badge.dart';
import '../../core/widgets/live_status_pill.dart';
import '../../core/widgets/sprint_badge.dart';
import 'f1_leaderboard_table.dart';

// Also re-fetches the cached prediction itself, same interval as
// event_detail_page.dart's own, which is enough to pick up a
// stale->fresh transition after the predict Lambda finishes a background
// recompute. f1LiveScoresProvider's own backend cache (live_scores.py) is
// refreshed on a 3-minute EventBridge tick (scheduler-f1-live-scores.tf),
// so this interval is plenty to catch a live session starting/ending.
const _pollInterval = Duration(seconds: 30);

/// F1's own detail page -- mirrors field_event_detail_page.dart's
/// Timer.periodic polling shell, but with no event_type branch at the
/// PAGE level (unlike PGA's field/match_play/cup split): both F1
/// event_types (field/sprint) render through the SAME leaderboard-table
/// shape, just with a different column set (F1LeaderboardTable's own
/// isSprint flag) and "field" alone additionally shows a constructors
/// panel. Also watches f1LiveScoresProvider (ESPN-sourced -- see
/// f1_live_score.dart's own docstring) and feeds this event's own live
/// state down to _PredictionView, same split field_event_detail_page.dart
/// uses for its own fieldLiveScoresProvider watch.
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
    ref.invalidate(f1LiveScoresProvider(widget.sportId));
    ref.invalidate(f1EventPredictionProvider((sport: widget.sportId, eventId: widget.eventId)));
  }

  @override
  Widget build(BuildContext context) {
    final liveScores = ref.watch(f1LiveScoresProvider(widget.sportId)).value ?? const <String, F1LiveEventState>{};
    final liveState = liveScores[widget.eventId];
    return SingleChildScrollView(
      padding: const EdgeInsets.all(24),
      child: ref.watch(f1EventPredictionProvider((sport: widget.sportId, eventId: widget.eventId))).when(
            data: (prediction) => _PredictionView(sport: widget.sportId, eventId: widget.eventId, prediction: prediction, liveState: liveState),
            loading: () => const Center(child: CircularProgressIndicator()),
            error: (error, _) => error is PredictionComputingException
                ? F1PredictionComputingRetry(sport: widget.sportId, eventId: widget.eventId, retryAfterSeconds: error.retryAfterSeconds)
                : Text('Couldn\'t load prediction: $error', style: AppTextStyles.body(color: AppColors.neg)),
          ),
    );
  }
}

enum _DetailTab { drivers, constructors }

// Not shared with f1_season_page.dart's own longer "...' Championship"
// form (a section-header, not a tab label) -- deliberately different
// copy for a different context, but named here instead of typed inline
// in _DetailTabToggle's own 2 calls below.
abstract final class _DetailTabLabels {
  static const drivers = 'Drivers';
  static const constructors = 'Constructors';
}

class _PredictionView extends StatefulWidget {
  const _PredictionView({required this.sport, required this.eventId, required this.prediction, this.liveState});

  final String sport;
  final String eventId;
  final F1EventPrediction prediction;
  // From f1LiveScoresProvider -- null when there's no live/recently-live
  // ESPN session for this event at all (the ordinary case).
  final F1LiveEventState? liveState;

  @override
  State<_PredictionView> createState() => _PredictionViewState();
}

class _PredictionViewState extends State<_PredictionView> {
  _DetailTab _tab = _DetailTab.drivers;

  void _setTab(_DetailTab tab) => setState(() => _tab = tab);

  @override
  Widget build(BuildContext context) {
    final prediction = widget.prediction;
    final hasConstructors = prediction.constructors.isNotEmpty;
    // Sprint has no constructors block at all -- stay on Drivers rather
    // than land on a tab with nothing to show.
    final tab = hasConstructors ? _tab : _DetailTab.drivers;

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
            if (widget.liveState?.isLive ?? false) const LiveStatusPill(),
            if (prediction.isSprint) ...[
              const SizedBox(width: 8),
              const SprintBadge(),
            ],
          ],
        ),
        if (prediction.stale) ...[
          const SizedBox(height: 12),
          F1PredictionFreshnessBadge(
            sport: widget.sport, eventId: widget.eventId, stale: prediction.stale, retryAfterSeconds: prediction.staleRetryAfterSeconds,
          ),
        ],
        // Own Drivers/Constructors tab toggle, same pill shape f1_season_
        // page.dart's own standings toggle uses -- a stacked driver list
        // (up to ~20 rows) plus a constructors table below it was a lot
        // to scroll through just to compare constructors (real complaint
        // 2026-09-01). Toggle only shown when there's a constructors
        // block to switch to at all.
        if (hasConstructors) ...[
          const SizedBox(height: 16),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              _DetailTabToggle(
                label: _DetailTabLabels.drivers, selected: tab == _DetailTab.drivers, onTap: () => _setTab(_DetailTab.drivers),
              ),
              _DetailTabToggle(
                label: _DetailTabLabels.constructors, selected: tab == _DetailTab.constructors, onTap: () => _setTab(_DetailTab.constructors),
              ),
            ],
          ),
        ],
        const SizedBox(height: 16),
        if (tab == _DetailTab.drivers)
          F1LeaderboardTable(
            field: prediction.field, isSprint: prediction.isSprint, liveResults: widget.liveState?.participants ?? const {},
          )
        else
          _ConstructorsTable(constructors: prediction.constructors),
      ],
    );
  }
}

class _DetailTabToggle extends StatelessWidget {
  const _DetailTabToggle({required this.label, required this.selected, required this.onTap});
  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(999),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
        decoration: BoxDecoration(
          color: selected ? AppColors.surface : null,
          border: Border.all(color: selected ? AppColors.violet : AppColors.border),
          borderRadius: BorderRadius.circular(999),
        ),
        child: Text(label, style: AppTextStyles.microLabel(color: selected ? AppColors.violet : AppColors.inkMute)),
      ),
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
                      constructors[i].name ?? humanizeF1EntityId(constructors[i].entityId),
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

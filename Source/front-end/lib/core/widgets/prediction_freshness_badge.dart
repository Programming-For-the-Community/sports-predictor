import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../data/events_repository.dart';
import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';

/// Sits alongside an already-rendered prediction (unlike
/// PredictionComputingRetry, which replaces it) -- shown when predict-read
/// served a 203 (see EventPrediction.stale): a small "updating" indicator,
/// plus the mechanism that actually resolves it. Schedules a silent
/// background eventPredictionProvider invalidate after retryAfterSeconds;
/// the existing (stale-but-still-good) prediction stays on screen until
/// the next fetch lands. Reschedules itself (didUpdateWidget) if that next
/// fetch is still stale, since Riverpod's default skipLoadingOnRefresh
/// keeps rendering this same widget instance across a refetch. Renders
/// (and schedules) nothing at all when not stale.
class PredictionFreshnessBadge extends ConsumerStatefulWidget {
  const PredictionFreshnessBadge({
    super.key,
    required this.sport,
    required this.eventId,
    required this.stale,
    this.retryAfterSeconds,
    this.compact = false,
  });

  final String sport;
  final String eventId;
  final bool stale;
  // Server-declared cadence (EventPrediction.staleRetryAfterSeconds) --
  // falls back to 5s if a stale response ever omits it.
  final int? retryAfterSeconds;
  // Compact renders as a short inline caption (game_row.dart's list
  // card) -- non-compact is the full pill (matchup_hero.dart).
  final bool compact;

  @override
  ConsumerState<PredictionFreshnessBadge> createState() => _PredictionFreshnessBadgeState();
}

class _PredictionFreshnessBadgeState extends ConsumerState<PredictionFreshnessBadge> {
  Timer? _refreshTimer;

  @override
  void initState() {
    super.initState();
    _scheduleIfStale();
  }

  @override
  void didUpdateWidget(covariant PredictionFreshnessBadge oldWidget) {
    super.didUpdateWidget(oldWidget);
    _scheduleIfStale();
  }

  void _scheduleIfStale() {
    _refreshTimer?.cancel();
    if (!widget.stale) return;
    _refreshTimer = Timer(Duration(seconds: widget.retryAfterSeconds ?? 5), () {
      if (!mounted) return;
      ref.invalidate(eventPredictionProvider((sport: widget.sport, eventId: widget.eventId)));
    });
  }

  @override
  void dispose() {
    _refreshTimer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (!widget.stale) return const SizedBox.shrink();

    if (widget.compact) {
      return Text('Updating…', style: AppTextStyles.microLabel(color: AppColors.inkMute));
    }
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(color: AppColors.inkMute.withValues(alpha: 0.15), borderRadius: BorderRadius.circular(999)),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          SizedBox(width: 10, height: 10, child: CircularProgressIndicator(strokeWidth: 1.5, color: AppColors.inkMute)),
          const SizedBox(width: 6),
          Text('UPDATING', style: AppTextStyles.microLabel(color: AppColors.inkMute)),
        ],
      ),
    );
  }
}

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../data/field_events_repository.dart';
import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';

/// PGA's own equivalent of prediction_freshness_badge.dart, invalidating
/// fieldEventPredictionProvider instead -- see
/// field_prediction_computing_retry.dart's own doc comment for why this
/// is a separate file rather than a generalized shared one. No compact
/// variant, same reasoning as that file.
class FieldPredictionFreshnessBadge extends ConsumerStatefulWidget {
  const FieldPredictionFreshnessBadge({super.key, required this.sport, required this.eventId, required this.stale, this.retryAfterSeconds});

  final String sport;
  final String eventId;
  final bool stale;
  final int? retryAfterSeconds;

  @override
  ConsumerState<FieldPredictionFreshnessBadge> createState() => _FieldPredictionFreshnessBadgeState();
}

class _FieldPredictionFreshnessBadgeState extends ConsumerState<FieldPredictionFreshnessBadge> {
  Timer? _refreshTimer;

  @override
  void initState() {
    super.initState();
    _scheduleIfStale();
  }

  @override
  void didUpdateWidget(covariant FieldPredictionFreshnessBadge oldWidget) {
    super.didUpdateWidget(oldWidget);
    _scheduleIfStale();
  }

  void _scheduleIfStale() {
    _refreshTimer?.cancel();
    if (!widget.stale) return;
    _refreshTimer = Timer(Duration(seconds: widget.retryAfterSeconds ?? 5), () {
      if (!mounted) return;
      ref.invalidate(fieldEventPredictionProvider((sport: widget.sport, eventId: widget.eventId)));
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

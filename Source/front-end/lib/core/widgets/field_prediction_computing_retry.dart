import 'dart:async';
import 'dart:math';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../data/field_events_repository.dart';
import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';

/// PGA's own equivalent of prediction_computing_retry.dart, invalidating
/// fieldEventPredictionProvider instead of the head-to-head
/// eventPredictionProvider -- kept as a separate file rather than
/// generalizing the existing one, matching this whole PGA pass's "parallel
/// files, head-to-head internals untouched" approach (see
/// field_event_row.dart, field_leaderboard_table.dart). No compact
/// variant -- field_event_list_page.dart never fetches a prediction per
/// row, so this is only ever shown full-size on the detail page.
class FieldPredictionComputingRetry extends ConsumerStatefulWidget {
  const FieldPredictionComputingRetry({super.key, required this.sport, required this.eventId, required this.retryAfterSeconds});

  final String sport;
  final String eventId;
  final int retryAfterSeconds;

  @override
  ConsumerState<FieldPredictionComputingRetry> createState() => _FieldPredictionComputingRetryState();
}

class _FieldPredictionComputingRetryState extends ConsumerState<FieldPredictionComputingRetry> {
  Timer? _retryTimer;
  final Random _random = Random();

  @override
  void initState() {
    super.initState();
    _scheduleRetry();
  }

  @override
  void didUpdateWidget(covariant FieldPredictionComputingRetry oldWidget) {
    super.didUpdateWidget(oldWidget);
    _scheduleRetry();
  }

  // +0-40% jitter, same reasoning as prediction_computing_retry.dart's own.
  void _scheduleRetry() {
    _retryTimer?.cancel();
    final baseMs = widget.retryAfterSeconds * 1000;
    final jitteredMs = baseMs + _random.nextInt((baseMs * 0.4).round() + 1);
    _retryTimer = Timer(Duration(milliseconds: jitteredMs), () {
      if (!mounted) return;
      ref.invalidate(fieldEventPredictionProvider((sport: widget.sport, eventId: widget.eventId)));
    });
  }

  @override
  void dispose() {
    _retryTimer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2)),
          const SizedBox(height: 12),
          Text('Computing prediction...', style: AppTextStyles.body(color: AppColors.inkSub)),
        ],
      ),
    );
  }
}

import 'dart:async';
import 'dart:math';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../data/f1_events_repository.dart';
import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';

/// F1's own equivalent of field_prediction_computing_retry.dart,
/// invalidating f1EventPredictionProvider instead -- same "parallel
/// files, don't generalize the shared one" precedent that file's own doc
/// comment establishes.
class F1PredictionComputingRetry extends ConsumerStatefulWidget {
  const F1PredictionComputingRetry({super.key, required this.sport, required this.eventId, required this.retryAfterSeconds});

  final String sport;
  final String eventId;
  final int retryAfterSeconds;

  @override
  ConsumerState<F1PredictionComputingRetry> createState() => _F1PredictionComputingRetryState();
}

class _F1PredictionComputingRetryState extends ConsumerState<F1PredictionComputingRetry> {
  Timer? _retryTimer;
  final Random _random = Random();

  @override
  void initState() {
    super.initState();
    _scheduleRetry();
  }

  @override
  void didUpdateWidget(covariant F1PredictionComputingRetry oldWidget) {
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
      ref.invalidate(f1EventPredictionProvider((sport: widget.sport, eventId: widget.eventId)));
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

import 'dart:async';
import 'dart:math';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../data/events_repository.dart';
import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';

/// Shown in place of a prediction while predict-read's cache is cold (see
/// PredictionComputingException) -- the compute was already triggered
/// server-side on the request that surfaced this, so all this does is
/// wait `retryAfterSeconds` then invalidate eventPredictionProvider to
/// pick up the now-cached result. Reschedules on every rebuild
/// (didUpdateWidget), not just once in initState, so it keeps counting
/// down from a fresh PredictionComputingException if the compute is still
/// running when a retry lands. Doesn't re-trigger the compute itself on
/// each retry -- a single miss only ever starts one background compute
/// server-side, so repeated retries here are just re-reading the same
/// in-progress cache entry until it resolves.
class PredictionComputingRetry extends ConsumerStatefulWidget {
  const PredictionComputingRetry({
    super.key,
    required this.sport,
    required this.eventId,
    required this.retryAfterSeconds,
    this.compact = false,
  });

  final String sport;
  final String eventId;
  final int retryAfterSeconds;
  // Compact renders inline (game_row.dart's list card, one prediction
  // slot among several rows) -- non-compact takes a full centered block
  // (event_detail_page.dart, the only thing on that part of the page).
  final bool compact;

  @override
  ConsumerState<PredictionComputingRetry> createState() => _PredictionComputingRetryState();
}

class _PredictionComputingRetryState extends ConsumerState<PredictionComputingRetry> {
  Timer? _retryTimer;
  final Random _random = Random();

  @override
  void initState() {
    super.initState();
    _scheduleRetry();
  }

  @override
  void didUpdateWidget(covariant PredictionComputingRetry oldWidget) {
    super.didUpdateWidget(oldWidget);
    _scheduleRetry();
  }

  // +0-40% jitter on top of the server's own retryAfterSeconds -- every
  // card on a page whose caches went stale/missing at the same moment
  // otherwise gets the exact same delay and re-requests in the same tick,
  // over and over. Jitter spreads retries out instead of a synchronized
  // burst.
  void _scheduleRetry() {
    _retryTimer?.cancel();
    final baseMs = widget.retryAfterSeconds * 1000;
    final jitteredMs = baseMs + _random.nextInt((baseMs * 0.4).round() + 1);
    _retryTimer = Timer(Duration(milliseconds: jitteredMs), () {
      if (!mounted) return;
      ref.invalidate(eventPredictionProvider((sport: widget.sport, eventId: widget.eventId)));
    });
  }

  @override
  void dispose() {
    _retryTimer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final spinner = SizedBox(
      width: widget.compact ? 14 : 18,
      height: widget.compact ? 14 : 18,
      child: const CircularProgressIndicator(strokeWidth: 2),
    );
    final label = Text(
      'Computing prediction...',
      style: widget.compact
          ? AppTextStyles.body(color: AppColors.inkMute)
          : AppTextStyles.body(color: AppColors.inkSub),
    );

    if (widget.compact) {
      return Row(mainAxisSize: MainAxisSize.min, children: [spinner, const SizedBox(width: 8), label]);
    }
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [spinner, const SizedBox(height: 12), label],
      ),
    );
  }
}

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/data/events_repository.dart';
import '../../core/data/live_scores_repository.dart';
import '../../core/models/event.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/matchup_hero.dart';
import '../../core/widgets/prediction_computing_retry.dart';
import '../../core/widgets/prediction_freshness_badge.dart';
import '../../core/widgets/team_leaders_panel.dart';
import '../../static/nfl_team_colors.dart';

// Mirrors live_scores.py's own POLL_START_BEFORE_KICKOFF/
// POLL_SAFETY_CAP_AFTER_KICKOFF window -- re-fetching the prediction
// outside this window would just repeat the exact same live-compute
// backend call for a game whose relevant inputs (roster, injuries, Elo)
// essentially never change minute to minute this far from kickoff.
const _predictionPollWindowBefore = Duration(minutes: 15);
const _predictionPollWindowAfter = Duration(hours: 7);

// Same cadence as event_list_page.dart's own live-scores poll.
const _pollInterval = Duration(seconds: 30);

bool _withinPredictionPollWindow(SportEvent event) {
  final kickoff = DateTime.tryParse(event.kickoffTime ?? '');
  if (kickoff == null) return false;
  final now = DateTime.now().toUtc();
  return now.isAfter(kickoff.subtract(_predictionPollWindowBefore)) && now.isBefore(kickoff.add(_predictionPollWindowAfter));
}

/// Event-level predictions (win probability, margin, home/away score) plus
/// player leaders per team -- see core/models/event_leaders.dart.
/// `EventPrediction.leaders` stays nullable since it's a best-effort field
/// server-side (see handler.py's _predict_event_leaders): the panel simply
/// doesn't render if the backend couldn't compute it for a given event.
///
/// A completed event does NOT call the live prediction endpoint -- same
/// rule game_row.dart's own docstring documents for the list view: a
/// fresh "live" prediction for an already-played game is built from
/// rolling stats that may already include this game's own now-normalized
/// result, making it a misleading, circular-looking number, not an
/// honest pre-game prediction. Completed games instead use
/// event.predictionComparison, the prediction actually logged before the
/// game was played (already fetched as part of the completed events
/// list, no extra request needed) -- see MatchupResultHero.
///
/// Polls live scores/prediction every 30s while the event is scheduled --
/// without this, both stay frozen at whatever they were on page load for
/// as long as this page stays open (Riverpod caches the fetched value,
/// there's no time-based TTL), which is stale during a live game and
/// also meant this page never made another API call on its own, letting
/// AuthRepository's inactivityTtl clock expire under someone who never
/// stopped actively reading the page (see auth_repository.dart's
/// recordActivity for the other half of that fix).
class EventDetailPage extends ConsumerStatefulWidget {
  const EventDetailPage({super.key, required this.sportId, required this.eventId});

  final String sportId;
  final String eventId;

  @override
  ConsumerState<EventDetailPage> createState() => _EventDetailPageState();
}

class _EventDetailPageState extends ConsumerState<EventDetailPage> {
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

  SportEvent? _findEvent(List<SportEvent> events) {
    for (final event in events) {
      if (event.eventId == widget.eventId) return event;
    }
    return null;
  }

  // Only polls once the event is known AND still scheduled -- a
  // completed event (or one that hasn't loaded yet) has nothing here
  // worth refreshing.
  void _poll() {
    final scheduled = ref.read(eventsListProvider((sport: widget.sportId, status: 'scheduled'))).value ?? const <SportEvent>[];
    final event = _findEvent(scheduled);
    if (event == null) return;

    ref.invalidate(liveScoresProvider(widget.sportId));
    if (_withinPredictionPollWindow(event)) {
      ref.invalidate(eventPredictionProvider((sport: widget.sportId, eventId: widget.eventId)));
    }
  }

  @override
  Widget build(BuildContext context) {
    // There's no "get one event" route -- the event could be in either
    // list depending on whether it's already been played, so both are
    // checked rather than assuming 'scheduled'.
    final scheduledAsync = ref.watch(eventsListProvider((sport: widget.sportId, status: 'scheduled')));
    final completedAsync = ref.watch(eventsListProvider((sport: widget.sportId, status: 'completed')));

    if (scheduledAsync.isLoading || completedAsync.isLoading) {
      return const Center(child: CircularProgressIndicator());
    }
    // Surface a real error instead of silently treating it as "no
    // events" and misreporting "Event not found" for what might be a
    // session/network failure.
    if (scheduledAsync.hasError) {
      return Text('Couldn\'t load events: ${scheduledAsync.error}', style: AppTextStyles.body(color: AppColors.neg));
    }
    if (completedAsync.hasError) {
      return Text('Couldn\'t load events: ${completedAsync.error}', style: AppTextStyles.body(color: AppColors.neg));
    }

    final scheduled = scheduledAsync.value ?? const <SportEvent>[];
    final completed = completedAsync.value ?? const <SportEvent>[];
    final event = _findEvent(scheduled) ?? _findEvent(completed);

    if (event == null) {
      return Text('Event not found.', style: AppTextStyles.body(color: AppColors.neg));
    }

    if (event.status == 'completed') {
      final leadersComparison = event.leadersComparison;
      return SingleChildScrollView(
        padding: const EdgeInsets.all(24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            MatchupResultHero(sport: widget.sportId, event: event, comparison: event.predictionComparison),
            if (leadersComparison != null) ...[
              const SizedBox(height: 20),
              TeamLeadersComparisonPanel(
                homeAbbr: teamDisplay(widget.sportId, event.home).abbreviation,
                awayAbbr: teamDisplay(widget.sportId, event.away).abbreviation,
                comparison: leadersComparison,
              ),
            ],
          ],
        ),
      );
    }

    return SingleChildScrollView(
      padding: const EdgeInsets.all(24),
      child: ref.watch(eventPredictionProvider((sport: widget.sportId, eventId: widget.eventId))).when(
            data: (prediction) {
              final leaders = prediction.leaders;
              final liveScores = ref.watch(liveScoresProvider(widget.sportId)).value ?? const {};
              return Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  MatchupHero(
                    sport: widget.sportId, event: event, prediction: prediction, liveState: liveScores[widget.eventId],
                  ),
                  if (prediction.stale) ...[
                    const SizedBox(height: 12),
                    Center(
                      child: PredictionFreshnessBadge(
                        sport: widget.sportId, eventId: widget.eventId,
                        stale: prediction.stale, retryAfterSeconds: prediction.staleRetryAfterSeconds,
                      ),
                    ),
                  ],
                  if (leaders != null) ...[
                    const SizedBox(height: 20),
                    TeamLeadersPanel(
                      homeAbbr: teamDisplay(widget.sportId, event.home).abbreviation,
                      awayAbbr: teamDisplay(widget.sportId, event.away).abbreviation,
                      leaders: leaders,
                    ),
                  ],
                ],
              );
            },
            loading: () => const Center(child: CircularProgressIndicator()),
            error: (error, _) => error is PredictionComputingException
                ? PredictionComputingRetry(
                    sport: widget.sportId, eventId: widget.eventId, retryAfterSeconds: error.retryAfterSeconds,
                  )
                : Text('Couldn\'t load prediction: $error', style: AppTextStyles.body(color: AppColors.neg)),
          ),
    );
  }
}

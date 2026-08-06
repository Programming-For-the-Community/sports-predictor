import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/data/events_repository.dart';
import '../../core/models/event.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/matchup_hero.dart';
import '../../core/widgets/team_leaders_panel.dart';
import '../../static/nfl_team_colors.dart';

/// Event-level predictions (win probability, margin, home/away score) plus
/// player leaders per team -- see core/models/event_leaders.dart.
/// `EventPrediction.leaders` stays nullable since it's a best-effort field
/// server-side (see handler.py's _predict_event_leaders): the panel simply
/// doesn't render if the backend couldn't compute it for a given event.
///
/// A completed event does NOT call the live prediction endpoint -- same
/// rule game_row.dart's own docstring already documents for the list
/// view, which this page previously didn't follow: a fresh "live"
/// prediction for an already-played game is built from rolling stats
/// that may already include this game's own now-normalized result,
/// making it a misleading, circular-looking number, not an honest
/// pre-game prediction. Completed games instead use
/// event.predictionComparison, the prediction actually logged before the
/// game was played (already fetched as part of the completed events
/// list, no extra request needed) -- see MatchupResultHero.
class EventDetailPage extends ConsumerWidget {
  const EventDetailPage({super.key, required this.sportId, required this.eventId});

  final String sportId;
  final String eventId;

  SportEvent? _findEvent(List<SportEvent> events) {
    for (final event in events) {
      if (event.eventId == eventId) return event;
    }
    return null;
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    // There's no "get one event" route -- the event could be in either
    // list depending on whether it's already been played, so both are
    // checked rather than assuming 'scheduled'.
    final scheduledAsync = ref.watch(eventsListProvider((sport: sportId, status: 'scheduled')));
    final completedAsync = ref.watch(eventsListProvider((sport: sportId, status: 'completed')));

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
            MatchupResultHero(event: event, comparison: event.predictionComparison),
            if (leadersComparison != null) ...[
              const SizedBox(height: 20),
              TeamLeadersComparisonPanel(
                homeAbbr: nflTeam(event.home.entityId).abbreviation,
                awayAbbr: nflTeam(event.away.entityId).abbreviation,
                comparison: leadersComparison,
              ),
            ],
          ],
        ),
      );
    }

    return SingleChildScrollView(
      padding: const EdgeInsets.all(24),
      child: ref.watch(eventPredictionProvider((sport: sportId, eventId: eventId))).when(
            data: (prediction) {
              final leaders = prediction.leaders;
              return Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  MatchupHero(event: event, prediction: prediction),
                  if (leaders != null) ...[
                    const SizedBox(height: 20),
                    TeamLeadersPanel(
                      homeAbbr: nflTeam(event.home.entityId).abbreviation,
                      awayAbbr: nflTeam(event.away.entityId).abbreviation,
                      leaders: leaders,
                    ),
                  ],
                ],
              );
            },
            loading: () => const Center(child: CircularProgressIndicator()),
            error: (error, _) => Text('Couldn\'t load prediction: $error', style: AppTextStyles.body(color: AppColors.neg)),
          ),
    );
  }
}

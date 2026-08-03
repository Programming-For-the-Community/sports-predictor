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
    final predictionAsync = ref.watch(eventPredictionProvider((sport: sportId, eventId: eventId)));

    return SingleChildScrollView(
      padding: const EdgeInsets.all(24),
      child: predictionAsync.when(
        data: (prediction) {
          if (scheduledAsync.isLoading || completedAsync.isLoading) {
            return const Center(child: CircularProgressIndicator());
          }

          final scheduled = scheduledAsync.maybeWhen(data: (events) => events, orElse: () => const <SportEvent>[]);
          final completed = completedAsync.maybeWhen(data: (events) => events, orElse: () => const <SportEvent>[]);
          final event = _findEvent(scheduled) ?? _findEvent(completed);

          if (event == null) {
            return Text('Event not found.', style: AppTextStyles.body(color: AppColors.neg));
          }

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

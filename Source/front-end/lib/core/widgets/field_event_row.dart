import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../data/field_events_repository.dart';
import '../models/event_status.dart';
import '../models/field_event.dart';
import '../models/field_prediction.dart';
import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';
import 'event_card.dart';

DateTime? _start(FieldEvent event) => DateTime.tryParse(event.eventDate);

DateTime? _end(FieldEvent event) => event.endDate == null ? null : DateTime.tryParse(event.endDate!);

/// "Aug 20-23, 2026" from event_date/end_date -- a single date when there
/// is no later end date.
String _dateRangeLabel(FieldEvent event) {
  final start = _start(event);
  if (start == null) return '';
  final end = _end(event);
  final startLabel = '${monthAbbreviations[start.month - 1]} ${start.day}';
  if (end == null || !end.isAfter(start)) return '$startLabel, ${start.year}';
  final endLabel = end.month == start.month ? '${end.day}' : '${monthAbbreviations[end.month - 1]} ${end.day}';
  return '$startLabel-$endLabel, ${end.year}';
}

const inProgressLabel = 'IN PROGRESS';

/// One tournament in GET /pga/events' list (a field event or a cup, never a
/// cup's own matches): its dates, name and venue, and when it starts -- or,
/// once played, the year. No per-row prediction; a field response is too
/// heavy to fetch for every row of a season.
class FieldEventRow extends StatelessWidget {
  const FieldEventRow({super.key, required this.sport, required this.event, this.now});

  final String sport;
  final FieldEvent event;
  // The current time; a test pins it.
  final DateTime? now;

  Widget? _trailing(DateTime? start) {
    if (start == null) return null;
    if (event.status == EventStatus.completed) {
      return Text('${start.year}', style: AppTextStyles.microLabel(color: AppColors.inkSub));
    }
    final startsIn = startsInLabel(start, now ?? DateTime.now());
    return Text(
      startsIn ?? inProgressLabel,
      style: AppTextStyles.microLabel(color: startsIn == null ? AppColors.live : AppColors.inkSub),
    );
  }

  @override
  Widget build(BuildContext context) {
    final start = _start(event);
    return EventCard(
      sport: sport,
      eventId: event.eventId,
      title: Text(event.tournamentName ?? 'Tournament', style: AppTextStyles.body(color: AppColors.ink)),
      dateBlock: start == null ? null : dateBlockLabels(start, _end(event)),
      venueLabel: event.venueLabel,
      trailing: _trailing(start),
    );
  }
}

/// The next (or in-progress) tournament, with the three golfers the model
/// ranks highest and each one's chance of a top-10 finish. A cup has no
/// field to rank and shows without picks.
class FeaturedFieldEvent extends ConsumerWidget {
  const FeaturedFieldEvent({super.key, required this.sport, required this.event, this.now});

  static const picksLabel = 'TOP 10 CHANCE';

  final String sport;
  final FieldEvent event;
  final DateTime? now;

  static List<FeaturedPick> _picks(PgaEventPrediction prediction) => switch (prediction) {
        PgaFieldPrediction(:final prediction) => [
            for (final golfer in prediction.field.take(3))
              if (golfer.top10Probability != null)
                FeaturedPick(
                  name: golfer.name ?? golfer.entityId,
                  value: '${(golfer.top10Probability!.value * 100).round()}%',
                ),
          ],
        PgaTwoSidedPrediction() => const [],
      };

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final start = _start(event);
    final startsIn = start == null ? null : startsInLabel(start, now ?? DateTime.now());
    final prediction = ref.watch(fieldEventPredictionProvider((sport: sport, eventId: event.eventId)));
    return FeaturedEventCard(
      sport: sport,
      eventId: event.eventId,
      eyebrow: startsIn == null ? inProgressLabel : 'NEXT · $startsIn',
      eyebrowColor: startsIn == null ? AppColors.live : AppColors.violet,
      title: event.tournamentName ?? 'Tournament',
      subtitle: _dateRangeLabel(event),
      venueLabel: event.venueLabel,
      picksLabel: picksLabel,
      picks: prediction.whenData(_picks),
    );
  }
}

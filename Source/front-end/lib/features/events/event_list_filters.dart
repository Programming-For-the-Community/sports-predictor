import 'package:flutter/material.dart';

import '../../core/models/event.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/confidence_pill.dart';
import '../../core/widgets/game_row.dart' show clockLabel;
import '../../core/widgets/status_toggle.dart';

/// Every game kicking off in the same local hour.
class KickoffSlot {
  const KickoffSlot({required this.hour, required this.label});

  /// Local hour of day, 0-23.
  final int hour;

  /// "3:30 PM" when every game in the hour shares one kickoff time, else "3 PM".
  final String label;
}

DateTime? _localKickoff(SportEvent event) {
  final kickoff = event.kickoffTime;
  return kickoff == null ? null : DateTime.tryParse(kickoff)?.toLocal();
}

/// The local hour this event kicks off in -- null without a kickoff time.
int? kickoffHour(SportEvent event) => _localKickoff(event)?.hour;

/// The distinct kickoff slots in `events`, earliest hour first.
List<KickoffSlot> kickoffSlots(List<SportEvent> events) {
  final minutesByHour = <int, Set<int>>{};
  for (final kickoff in events.map(_localKickoff).whereType<DateTime>()) {
    minutesByHour.putIfAbsent(kickoff.hour, () => {}).add(kickoff.minute);
  }
  final hours = minutesByHour.keys.toList()..sort();
  return [
    for (final hour in hours)
      KickoffSlot(
        hour: hour,
        label: minutesByHour[hour]!.length == 1
            ? clockLabel(DateTime(2000, 1, 1, hour, minutesByHour[hour]!.single))
            : clockLabel(DateTime(2000, 1, 1, hour)).replaceFirst(':00', ''),
      ),
  ];
}

/// Confidence-tier and kickoff-slot chips for the Upcoming/Current list.
/// An empty selection in a row means that row doesn't filter.
class EventListFilters extends StatelessWidget {
  const EventListFilters({
    super.key,
    required this.slots,
    required this.selectedTiers,
    required this.selectedHours,
    required this.onToggleTier,
    required this.onToggleHour,
  });

  final List<KickoffSlot> slots;
  final Set<String> selectedTiers;
  final Set<int> selectedHours;
  final ValueChanged<String> onToggleTier;
  final ValueChanged<int> onToggleHour;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('WINNER CONFIDENCE', style: AppTextStyles.microLabel(color: AppColors.inkSub)),
        const SizedBox(height: 8),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            for (final tier in confidenceTiers)
              StatusToggle(
                label: tier,
                selected: selectedTiers.contains(tier),
                onTap: () => onToggleTier(tier),
                accentColor: AppColors.cyan,
              ),
          ],
        ),
        // A single slot has nothing to choose between.
        if (slots.length > 1) ...[
          const SizedBox(height: 12),
          Text('KICKOFF', style: AppTextStyles.microLabel(color: AppColors.inkSub)),
          const SizedBox(height: 8),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              for (final slot in slots)
                StatusToggle(
                  label: slot.label,
                  selected: selectedHours.contains(slot.hour),
                  onTap: () => onToggleHour(slot.hour),
                  accentColor: AppColors.cyan,
                ),
            ],
          ),
        ],
      ],
    );
  }
}

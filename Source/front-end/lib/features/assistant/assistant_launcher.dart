import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/data/events_repository.dart';
import '../../core/models/event.dart';
import '../../core/models/event_status.dart';
import '../../core/models/sport_config.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../static/nfl_team_colors.dart';
import 'assistant_controller.dart';
import 'assistant_models.dart';
import 'assistant_panel.dart';

/// Wider than this, the assistant docks beside the page; narrower, it opens
/// as a bottom sheet.
const assistantDockMinWidth = 900.0;
const assistantDockWidth = 400.0;

bool assistantDocks(BuildContext context) => MediaQuery.sizeOf(context).width >= assistantDockMinWidth;

/// Where the user is: the sport, and on an event page the event and its
/// "AWAY @ HOME" label, read from the events list that page already loaded.
/// No sport (the home page): no context, so the agent asks which sport.
AssistantContext assistantContextFor(WidgetRef ref, String? sportId, String? eventId) {
  if (sportId == null) return const AssistantContext();
  if (eventId == null) return AssistantContext(sport: sportId);
  String label = 'THIS EVENT';
  if (sportById(sportId).eventShape == EventShape.headToHead) {
    final events = [
      ...?ref.read(eventsListProvider((sport: sportId, status: EventStatus.scheduled))).value,
      ...?ref.read(eventsListProvider((sport: sportId, status: EventStatus.completed))).value,
    ];
    for (final SportEvent event in events) {
      if (event.eventId == eventId) {
        label = '${teamDisplay(sportId, event.away).abbreviation} @ ${teamDisplay(sportId, event.home).abbreviation}';
        break;
      }
    }
  }
  return AssistantContext(sport: sportId, eventId: eventId, eventLabel: label);
}

void openAssistant(BuildContext context, WidgetRef ref, AssistantContext assistantContext) {
  if (assistantDocks(context)) {
    final open = ref.read(assistantDockOpenProvider.notifier);
    open.state = !open.state;
    return;
  }
  showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    useSafeArea: true,
    backgroundColor: AppColors.menu,
    shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(24))),
    builder: (sheetContext) => FractionallySizedBox(
      heightFactor: 0.9,
      child: Padding(
        padding: EdgeInsets.only(bottom: MediaQuery.viewInsetsOf(sheetContext).bottom),
        child: AssistantPanel(context: assistantContext, onClose: () => Navigator.of(sheetContext).pop()),
      ),
    ),
  );
}

/// Phone: the round "Ask" button floating over the page.
class AssistantFab extends ConsumerWidget {
  const AssistantFab({super.key, this.sportId, this.eventId});
  final String? sportId;
  final String? eventId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return FloatingActionButton.extended(
      heroTag: 'assistant',
      tooltip: 'Ask the assistant',
      backgroundColor: AppColors.cyan,
      foregroundColor: AppColors.bg,
      onPressed: () => openAssistant(context, ref, assistantContextFor(ref, sportId, eventId)),
      icon: const Icon(Icons.chat_bubble_outline),
      label: Text('Ask', style: AppTextStyles.sectionTitle(color: AppColors.bg)),
    );
  }
}

/// Wide screens: the outlined "Ask" toggle in the page header.
class AssistantToggle extends ConsumerWidget {
  const AssistantToggle({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final open = ref.watch(assistantDockOpenProvider);
    return OutlinedButton.icon(
      onPressed: () => ref.read(assistantDockOpenProvider.notifier).state = !open,
      icon: const Icon(Icons.chat_bubble_outline, size: 18),
      label: Text('Ask', style: AppTextStyles.body(color: AppColors.cyan).copyWith(fontWeight: FontWeight.w600)),
      style: OutlinedButton.styleFrom(
        foregroundColor: AppColors.cyan,
        backgroundColor: open ? AppColors.cyan.withValues(alpha: 0.12) : Colors.transparent,
        side: BorderSide(color: open ? AppColors.cyan : AppColors.borderRaised),
        shape: const StadiumBorder(),
        minimumSize: const Size(0, 40),
      ),
    );
  }
}

/// Wide screens: the page with the docked panel beside it when open.
class AssistantDock extends ConsumerWidget {
  const AssistantDock({super.key, this.sportId, this.eventId, required this.child});
  final String? sportId;
  final String? eventId;
  final Widget child;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final open = ref.watch(assistantDockOpenProvider) && assistantDocks(context);
    if (!open) return child;
    return Row(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Expanded(child: child),
        Container(
          width: assistantDockWidth,
          decoration: const BoxDecoration(border: Border(left: BorderSide(color: AppColors.borderRaised))),
          child: AssistantPanel(
            key: ValueKey('dock-$sportId-$eventId'),
            context: assistantContextFor(ref, sportId, eventId),
            onClose: () => ref.read(assistantDockOpenProvider.notifier).state = false,
          ),
        ),
      ],
    );
  }
}

import 'package:flutter/material.dart';

import '../../core/models/event.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
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

const _weekdayLabels = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN'];

/// "MON".."SUN" for a DateTime.weekday value (1-7).
String weekdayLabel(int weekday) => _weekdayLabels[weekday - 1];

/// The local day of the week (DateTime.monday..sunday) this event falls on,
/// from the same kickoff-else-date key the list's own day headings use.
int? eventWeekday(SportEvent event) => DateTime.tryParse(event.kickoffTime ?? event.eventDate)?.toLocal().weekday;

/// The distinct weekdays in `events`, in the order they first appear.
List<int> eventWeekdays(List<SportEvent> events) => events.map(eventWeekday).whereType<int>().toSet().toList();

/// One selectable value in a filter group.
class FilterChoice {
  const FilterChoice({required this.label, required this.isSelected, required this.onToggle, this.count});

  final String label;
  // Read when drawn, so an open menu or sheet follows each toggle.
  final bool Function() isSelected;
  final VoidCallback onToggle;
  // How many listed games have this value; null leaves the count off.
  final int? count;
}

/// A named set of choices; a group with none selected filters nothing.
class FilterGroup {
  const FilterGroup({required this.label, required this.choices});

  final String label;
  final List<FilterChoice> choices;

  List<FilterChoice> get selected => [for (final choice in choices) if (choice.isSelected()) choice];
}

// Below this width the groups fold into one Filters button and a sheet.
const _sheetBreakpoint = 600.0;

/// The event list's filters: one menu per group on a wide screen, one
/// Filters button opening a sheet on a phone. Only active choices stay on
/// screen.
class EventListFilters extends StatelessWidget {
  const EventListFilters({
    super.key,
    required this.groups,
    required this.shown,
    required this.total,
    required this.onClear,
    this.accentColor = AppColors.cyan,
  });

  static const filtersLabel = 'FILTERS';
  static const clearLabel = 'CLEAR';
  static const doneLabel = 'Done';

  final List<FilterGroup> groups;
  // Games left after filtering, and before it.
  final int shown;
  final int total;
  final VoidCallback onClear;
  final Color accentColor;

  int get _activeCount => groups.fold(0, (sum, group) => sum + group.selected.length);

  Widget _summary() => Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text('$shown OF $total GAMES', style: AppTextStyles.microLabel(color: AppColors.inkSub)),
          const SizedBox(width: 10),
          InkWell(
            onTap: onClear,
            borderRadius: BorderRadius.circular(999),
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 6),
              child: Text(clearLabel, style: AppTextStyles.microLabel(color: accentColor)),
            ),
          ),
        ],
      );

  Widget _wide() => Wrap(
        spacing: 8,
        runSpacing: 8,
        crossAxisAlignment: WrapCrossAlignment.center,
        children: [
          for (final group in groups) _GroupMenu(group: group, accentColor: accentColor),
          if (_activeCount > 0) _summary(),
        ],
      );

  Widget _narrow(BuildContext context) {
    final active = _activeCount;
    return Wrap(
      spacing: 8,
      runSpacing: 8,
      crossAxisAlignment: WrapCrossAlignment.center,
      children: [
        _MenuPill(
          label: active == 0 ? filtersLabel : '$filtersLabel · $active',
          selected: active > 0,
          accentColor: accentColor,
          icon: Icons.tune,
          onTap: () => showModalBottomSheet<void>(
            context: context,
            backgroundColor: AppColors.menu,
            isScrollControlled: true,
            builder: (context) => _FilterSheet(groups: groups, accentColor: accentColor),
          ),
        ),
        for (final group in groups)
          for (final choice in group.selected)
            _MenuPill(
              label: choice.label, selected: true, accentColor: accentColor, icon: Icons.close, onTap: choice.onToggle,
            ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    if (groups.isEmpty) return const SizedBox.shrink();
    return LayoutBuilder(
      builder: (context, constraints) => constraints.maxWidth < _sheetBreakpoint ? _narrow(context) : _wide(),
    );
  }
}

/// A pill with a trailing icon -- a menu button, or an active choice with
/// its remove mark.
class _MenuPill extends StatelessWidget {
  const _MenuPill({
    required this.label, required this.selected, required this.accentColor, required this.icon, required this.onTap,
  });

  final String label;
  final bool selected;
  final Color accentColor;
  final IconData icon;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final color = selected ? accentColor : AppColors.inkSub;
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(999),
      child: Container(
        padding: const EdgeInsets.only(left: 14, right: 10, top: 7, bottom: 7),
        decoration: BoxDecoration(
          color: selected ? AppColors.surface : null,
          border: Border.all(color: selected ? accentColor : AppColors.border),
          borderRadius: BorderRadius.circular(999),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Flexible(child: Text(label, style: AppTextStyles.microLabel(color: color))),
            const SizedBox(width: 4),
            Icon(icon, size: 14, color: color),
          ],
        ),
      ),
    );
  }
}

/// One group's button and its multi-select list.
class _GroupMenu extends StatelessWidget {
  const _GroupMenu({required this.group, required this.accentColor});

  final FilterGroup group;
  final Color accentColor;

  String get _buttonLabel {
    final selected = group.selected;
    if (selected.isEmpty) return group.label;
    return selected.length == 1 ? '${group.label} · ${selected.single.label}' : '${group.label} · ${selected.length}';
  }

  @override
  Widget build(BuildContext context) {
    return MenuAnchor(
      style: const MenuStyle(backgroundColor: WidgetStatePropertyAll(AppColors.menu)),
      menuChildren: [
        for (final choice in group.choices)
          CheckboxMenuButton(
            value: choice.isSelected(),
            closeOnActivate: false,
            onChanged: (_) => choice.onToggle(),
            child: ConstrainedBox(
              constraints: const BoxConstraints(minWidth: 130),
              child: Row(
                children: [
                  Expanded(child: Text(choice.label, style: AppTextStyles.body())),
                  if (choice.count != null) ...[
                    const SizedBox(width: 16),
                    Text('${choice.count}', style: AppTextStyles.microLabel(color: AppColors.inkSub)),
                  ],
                ],
              ),
            ),
          ),
      ],
      builder: (context, controller, _) => _MenuPill(
        label: _buttonLabel,
        selected: group.selected.isNotEmpty,
        accentColor: accentColor,
        icon: Icons.arrow_drop_down,
        onTap: () => controller.isOpen ? controller.close() : controller.open(),
      ),
    );
  }
}

/// Every group's choices as chips, on a phone.
class _FilterSheet extends StatefulWidget {
  const _FilterSheet({required this.groups, required this.accentColor});

  final List<FilterGroup> groups;
  final Color accentColor;

  @override
  State<_FilterSheet> createState() => _FilterSheetState();
}

class _FilterSheetState extends State<_FilterSheet> {
  @override
  Widget build(BuildContext context) {
    return SafeArea(
      child: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(20, 20, 20, 16),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            for (final group in widget.groups) ...[
              Text(group.label, style: AppTextStyles.microLabel(color: AppColors.inkSub)),
              const SizedBox(height: 8),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  for (final choice in group.choices)
                    StatusToggle(
                      label: choice.label,
                      selected: choice.isSelected(),
                      onTap: () => setState(choice.onToggle),
                      accentColor: widget.accentColor,
                    ),
                ],
              ),
              const SizedBox(height: 16),
            ],
            FilledButton(
              onPressed: () => Navigator.of(context).pop(),
              style: FilledButton.styleFrom(backgroundColor: widget.accentColor, foregroundColor: AppColors.bg),
              child: const Text(EventListFilters.doneLabel),
            ),
          ],
        ),
      ),
    );
  }
}

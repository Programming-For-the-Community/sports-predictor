import 'package:flutter/material.dart';

import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';

/// Upcoming/Completed pill toggle -- shared by event_list_page.dart (h2h),
/// f1_event_list_page.dart, and field_event_list_page.dart, which
/// previously each defined an identical private _StatusToggle class
/// differing only in their own sport's accent color.
class StatusToggle extends StatelessWidget {
  const StatusToggle({super.key, required this.label, required this.selected, required this.onTap, required this.accentColor});

  final String label;
  final bool selected;
  final VoidCallback onTap;
  final Color accentColor;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(999),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
        decoration: BoxDecoration(
          color: selected ? AppColors.surface : null,
          border: Border.all(color: selected ? accentColor : AppColors.border),
          borderRadius: BorderRadius.circular(999),
        ),
        child: Text(label, style: AppTextStyles.microLabel(color: selected ? accentColor : AppColors.inkMute)),
      ),
    );
  }
}

/// The 2 labels every StatusToggle pair in this app uses -- named once
/// instead of typed inline at each of the 3 call sites.
abstract final class StatusToggleLabels {
  static const upcoming = 'Upcoming/Current';
  static const completed = 'Completed';
}

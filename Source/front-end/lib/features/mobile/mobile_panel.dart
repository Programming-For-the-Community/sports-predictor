import 'package:flutter/material.dart';

import '../../core/theme/app_colors.dart';

/// The bordered surface behind the mobile prompts and settings sections.
/// [highlighted] adds the cyan-to-violet tint used for calls to action.
class MobilePanel extends StatelessWidget {
  const MobilePanel({super.key, required this.child, this.highlighted = false, this.padding = const EdgeInsets.all(16)});

  final Widget child;
  final bool highlighted;
  final EdgeInsets padding;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: padding,
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(14),
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: highlighted
              ? [AppColors.cyan.withValues(alpha: 0.10), AppColors.violet.withValues(alpha: 0.08)]
              : AppColors.surfaceGrad,
        ),
        border: Border.all(color: highlighted ? AppColors.cyan.withValues(alpha: 0.35) : AppColors.border),
      ),
      child: child,
    );
  }
}

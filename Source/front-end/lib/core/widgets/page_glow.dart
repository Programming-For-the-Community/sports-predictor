import 'package:flutter/material.dart';

import '../theme/app_colors.dart';

/// design/FRONTEND_STYLE.md: "a single non-interactive Container with the
/// radial gradient, positioned at the top-center of each page behind all
/// content... One glow per page." Place as the first child of a Stack.
class PageGlow extends StatelessWidget {
  const PageGlow({super.key});

  @override
  Widget build(BuildContext context) {
    return IgnorePointer(
      child: Align(
        alignment: Alignment.topCenter,
        child: Container(
          width: 1100,
          height: 560,
          decoration: const BoxDecoration(gradient: AppColors.glow),
        ),
      ),
    );
  }
}

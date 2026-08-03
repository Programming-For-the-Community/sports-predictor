import 'package:flutter/material.dart';

import '../theme/app_colors.dart';

/// design/FRONTEND_STYLE.md: "favored segment = cyan-fill, underdog =
/// translucent slate #33405580, track #1a2233, rounded ends."
class WinProbabilityBar extends StatelessWidget {
  const WinProbabilityBar({super.key, required this.homeWinProbability, this.height = 10});

  final double homeWinProbability;
  final double height;

  @override
  Widget build(BuildContext context) {
    final homeFavored = homeWinProbability >= 0.5;
    final homeFlex = (homeWinProbability * 1000).round().clamp(1, 999);

    return ClipRRect(
      borderRadius: BorderRadius.circular(999),
      child: SizedBox(
        height: height,
        child: DecoratedBox(
          decoration: const BoxDecoration(color: Color(0xFF1a2233)),
          child: Row(
            children: [
              Expanded(flex: homeFlex, child: _Segment(favored: homeFavored)),
              Expanded(flex: 1000 - homeFlex, child: _Segment(favored: !homeFavored)),
            ],
          ),
        ),
      ),
    );
  }
}

class _Segment extends StatelessWidget {
  const _Segment({required this.favored});
  final bool favored;

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: BoxDecoration(
        gradient: favored ? AppColors.cyanFill : null,
        color: favored ? null : const Color(0x80334055),
      ),
    );
  }
}

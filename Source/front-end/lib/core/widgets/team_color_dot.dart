import 'package:flutter/material.dart';

/// A team's primary color as a small dot. Several NFL teams' primary
/// colors are very dark (navy, black) -- nearly invisible on their own
/// against this app's own dark card background. Floors the fill's own HSL
/// lightness so a near-black team color reads as a visibly brighter (but
/// still recognizably that team's) shade, alongside a near-opaque ring.
class TeamColorDot extends StatelessWidget {
  const TeamColorDot({super.key, required this.color, this.size = 8});

  // Null for a team with no official color on record -- omits the dot
  // entirely rather than a guessed/hashed placeholder.
  final Color? color;
  final double size;

  // Below this, a color reads as "a hole in the background" rather than
  // a color swatch on this app's own dark surface.
  static const double _minFillLightness = 0.32;

  Color _visibleFill(Color color) {
    final hsl = HSLColor.fromColor(color);
    return hsl.lightness >= _minFillLightness ? color : hsl.withLightness(_minFillLightness).toColor();
  }

  @override
  Widget build(BuildContext context) {
    final fill = color;
    if (fill == null) return const SizedBox.shrink();
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        color: _visibleFill(fill),
        border: Border.all(color: Colors.white.withValues(alpha: 0.85), width: 1.2),
      ),
    );
  }
}

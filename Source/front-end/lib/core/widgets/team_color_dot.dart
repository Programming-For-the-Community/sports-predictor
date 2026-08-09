import 'package:flutter/material.dart';

/// A team's primary color as a small dot. Several NFL teams' primary
/// colors are very dark (navy, black) -- nearly invisible on their own
/// against this app's own dark card background. A light ring alone
/// wasn't enough contrast (confirmed live: still hard to make out against
/// the card background at 0.45 alpha) -- every instance now also floors
/// the FILL's own HSL lightness, so a near-black team color reads as a
/// visibly brighter (but still recognizably that team's) shade instead
/// of nearly merging with the ring or the background, alongside a
/// near-opaque ring.
class TeamColorDot extends StatelessWidget {
  const TeamColorDot({super.key, required this.color, this.size = 8});

  final Color color;
  final double size;

  // Below this, a color reads as "a hole in the background" rather than
  // a color swatch on this app's own dark surface -- picked by eye
  // against navy/black team colors (e.g. Seahawks/Patriots navy,
  // Ravens/Falcons/Panthers black), not a general-purpose contrast ratio.
  static const double _minFillLightness = 0.32;

  Color get _visibleFill {
    final hsl = HSLColor.fromColor(color);
    return hsl.lightness >= _minFillLightness ? color : hsl.withLightness(_minFillLightness).toColor();
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        color: _visibleFill,
        border: Border.all(color: Colors.white.withValues(alpha: 0.85), width: 1.2),
      ),
    );
  }
}

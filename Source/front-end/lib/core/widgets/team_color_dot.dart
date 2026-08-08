import 'package:flutter/material.dart';

/// A team's primary color as a small dot. Several NFL teams' primary
/// colors are very dark (navy, black) -- nearly invisible on their own
/// against this app's own dark card background -- so every instance
/// gets a light ring regardless of how dark the team color itself is.
class TeamColorDot extends StatelessWidget {
  const TeamColorDot({super.key, required this.color, this.size = 8});

  final Color color;
  final double size;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        color: color,
        border: Border.all(color: Colors.white.withValues(alpha: 0.45)),
      ),
    );
  }
}

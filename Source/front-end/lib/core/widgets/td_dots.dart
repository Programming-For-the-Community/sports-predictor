import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../theme/app_colors.dart';

/// Which way a touchdown prediction leans relative to the whole number shown.
enum TdLean {
  /// Just under the next TD (within 0.15), so it rounds up.
  up,

  /// Just past a whole TD (within 0.15), leaning away from the next one.
  away,

  /// 0.15-0.35 from the whole number shown.
  leaning,

  /// Near the half.
  tossUp,
}

TdLean tdLeanFor(double value) {
  final shown = value.round();
  final distance = (value - shown).abs();
  if (distance <= 0.15) return value < shown ? TdLean.up : TdLean.away;
  return distance <= 0.35 ? TdLean.leaning : TdLean.tossUp;
}

Color tdLeanColor(TdLean lean) => switch (lean) {
      TdLean.up => AppColors.live,
      TdLean.away => AppColors.neg,
      TdLean.leaning => AppColors.warn,
      TdLean.tossUp => AppColors.inkSub,
    };

const _dotSize = 11.0;
const _dotGap = 3.0;

/// A touchdown prediction's fraction as dots: whole TDs solid, the next dot
/// filled with the fraction, colored by tdLeanFor. `slots` is how many dots
/// to draw at minimum (2 for passing, 1 otherwise).
class TdDots extends StatelessWidget {
  const TdDots({super.key, required this.value, this.slots = 1});

  final double value;
  final int slots;

  int get _count => math.max(slots, value.ceil());

  /// The laid-out size -- for callers measuring a row before building it.
  Size get size => Size(_count * _dotSize + (_count - 1) * _dotGap, _dotSize);

  @override
  Widget build(BuildContext context) {
    final color = tdLeanColor(tdLeanFor(value));
    return Tooltip(
      message: '${value.toStringAsFixed(2)} TD predicted',
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          for (var i = 0; i < _count; i++) ...[
            if (i > 0) const SizedBox(width: _dotGap),
            CustomPaint(size: const Size(_dotSize, _dotSize), painter: _DotPainter((value - i).clamp(0.0, 1.0), color)),
          ],
        ],
      ),
    );
  }
}

class _DotPainter extends CustomPainter {
  const _DotPainter(this.fill, this.color);

  final double fill;
  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    const stroke = 1.5;
    final center = size.center(Offset.zero);
    final radius = size.shortestSide / 2;
    if (fill > 0) {
      canvas.drawArc(
        Rect.fromCircle(center: center, radius: radius),
        -math.pi / 2,
        fill * 2 * math.pi,
        true,
        Paint()..color = color,
      );
    }
    canvas.drawCircle(
      center,
      radius - stroke / 2,
      Paint()
        ..color = color
        ..style = PaintingStyle.stroke
        ..strokeWidth = stroke,
    );
  }

  @override
  bool shouldRepaint(_DotPainter old) => old.fill != fill || old.color != color;
}

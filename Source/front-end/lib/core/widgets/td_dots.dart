import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../theme/app_colors.dart';

/// A dot's color by how full it is: red when nearly empty (unlikely to count
/// as a TD), amber at half, green when full or nearly full (counts).
Color tdFillColor(double fill) {
  final f = fill.clamp(0.0, 1.0);
  return f <= 0.5
      ? Color.lerp(AppColors.neg, AppColors.warn, f / 0.5)!
      : Color.lerp(AppColors.warn, AppColors.live, (f - 0.5) / 0.5)!;
}

const _dotSize = 11.0;
const _dotGap = 3.0;

/// A touchdown prediction's fraction as dots: whole TDs solid green, the next
/// dot filled with the fraction and colored by tdFillColor, any slot left
/// over an empty outline. `slots` is how many dots to draw at minimum (2 for
/// passing, 1 otherwise).
class TdDots extends StatelessWidget {
  const TdDots({super.key, required this.value, this.slots = 1, this.unit = 'TD'});

  final double value;
  final int slots;
  final String unit;

  int get _count => math.max(slots, value.ceil());

  /// The laid-out size -- for callers measuring a row before building it.
  Size get size => Size(_count * _dotSize + (_count - 1) * _dotGap, _dotSize);

  @override
  Widget build(BuildContext context) {
    return Tooltip(
      message: '${value.toStringAsFixed(2)} $unit predicted',
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          for (var i = 0; i < _count; i++) ...[
            if (i > 0) const SizedBox(width: _dotGap),
            CustomPaint(size: const Size(_dotSize, _dotSize), painter: _DotPainter((value - i).clamp(0.0, 1.0))),
          ],
        ],
      ),
    );
  }
}

class _DotPainter extends CustomPainter {
  const _DotPainter(this.fill);

  final double fill;

  @override
  void paint(Canvas canvas, Size size) {
    const stroke = 1.5;
    final color = fill == 0 ? AppColors.inkMute : tdFillColor(fill);
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
  bool shouldRepaint(_DotPainter old) => old.fill != fill;
}

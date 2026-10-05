import 'package:flutter/material.dart';

import '../models/live_score.dart';
import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';

/// The small amber football marking which team has the ball in a live
/// football game (game_row.dart, matchup_hero.dart).
class PossessionBall extends StatelessWidget {
  const PossessionBall({super.key, this.width = 14});

  final double width;

  @override
  Widget build(BuildContext context) {
    return Semantics(
      label: 'Has the ball',
      child: CustomPaint(size: Size(width, width * 9 / 14), painter: const _BallPainter()),
    );
  }
}

class _BallPainter extends CustomPainter {
  const _BallPainter();

  @override
  void paint(Canvas canvas, Size size) {
    final glow = Paint()
      ..color = AppColors.warn.withValues(alpha: 0.45)
      ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 2);
    final body = Paint()..color = AppColors.warn;
    final oval = Rect.fromLTWH(0, 0, size.width, size.height);
    canvas.drawOval(oval, glow);
    canvas.drawOval(oval, body);

    final lace = Paint()
      ..color = Colors.white
      ..strokeWidth = size.height * 0.12
      ..strokeCap = StrokeCap.round;
    final y = size.height / 2;
    canvas.drawLine(Offset(size.width * 0.3, y), Offset(size.width * 0.7, y), lace);
    for (final x in [0.4, 0.5, 0.6]) {
      canvas.drawLine(Offset(size.width * x, y - size.height * 0.24), Offset(size.width * x, y + size.height * 0.24), lace);
    }
  }

  @override
  bool shouldRepaint(_BallPainter oldDelegate) => false;
}

/// "3rd & 4 · MIN 48 · Red zone" -- the down and distance in ink, field
/// position after it, and "Red zone" in red inside the 20. [separator]
/// joins the down and the field position (" · " in lists, " at " in the
/// event header).
class FootballSituationText extends StatelessWidget {
  const FootballSituationText({super.key, required this.situation, this.separator = ' · ', this.textAlign});

  final FootballSituation situation;
  final String separator;
  final TextAlign? textAlign;

  @override
  Widget build(BuildContext context) {
    final base = AppTextStyles.microLabel(color: AppColors.inkSub);
    final down = situation.downDistance;
    final field = situation.fieldPosition;
    return Text.rich(
      TextSpan(
        style: base,
        children: [
          if (down != null) TextSpan(text: down, style: base.copyWith(color: AppColors.ink)),
          if (down != null && field != null) TextSpan(text: separator),
          if (field != null) TextSpan(text: field),
          if (situation.redZone) ...[
            const TextSpan(text: ' · '),
            TextSpan(text: 'Red zone', style: base.copyWith(color: AppColors.neg, fontWeight: FontWeight.w600)),
          ],
        ],
      ),
      maxLines: 1,
      overflow: TextOverflow.ellipsis,
      textAlign: textAlign,
    );
  }
}

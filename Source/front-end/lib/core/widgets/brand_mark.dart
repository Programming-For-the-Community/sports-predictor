import 'package:flutter/material.dart';

import '../theme/app_colors.dart';

/// design/FRONTEND_STYLE.md's top-bar spec: "gradient brandMark tile (34px,
/// BorderRadius.circular(10))". Reused standalone on the login page at a
/// larger size for the same brand mark. Three ascending bars (trend/
/// prediction) in white, scaled proportionally to `size` -- same glyph
/// and gradient as the app's own favicon/PWA icons (see
/// front-end/web/icons/ and the generation script referenced in their
/// own commit), so the browser tab and the in-app header show the same
/// mark, not two different ones.
class BrandMark extends StatelessWidget {
  const BrandMark({super.key, this.size = 34});

  final double size;

  @override
  Widget build(BuildContext context) {
    final barWidth = size * 0.07;
    final gap = size * 0.035;
    final heights = [size * 0.11, size * 0.18, size * 0.26];

    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        gradient: AppColors.brandMark,
        borderRadius: BorderRadius.circular(size * 10 / 34),
      ),
      child: Center(
        child: Row(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.end,
          children: [
            for (var i = 0; i < heights.length; i++) ...[
              if (i > 0) SizedBox(width: gap),
              Container(
                width: barWidth,
                height: heights[i],
                decoration: BoxDecoration(
                  color: Colors.white,
                  borderRadius: BorderRadius.circular(barWidth * 0.3),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

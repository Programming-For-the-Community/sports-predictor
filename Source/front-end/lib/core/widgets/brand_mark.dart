import 'package:flutter/material.dart';

import '../theme/app_colors.dart';

/// design/FRONTEND_STYLE.md's top-bar spec: "gradient brandMark tile (34px,
/// BorderRadius.circular(10))". Reused standalone on the login page at a
/// larger size for the same brand mark.
class BrandMark extends StatelessWidget {
  const BrandMark({super.key, this.size = 34});

  final double size;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        gradient: AppColors.brandMark,
        borderRadius: BorderRadius.circular(size * 10 / 34),
      ),
    );
  }
}

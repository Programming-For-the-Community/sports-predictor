import 'package:flutter/material.dart';

import '../../core/theme/app_colors.dart';
import '../../core/widgets/brand_mark.dart';

/// Shown only while AuthState is AuthInitial (session restore/token
/// refresh still in flight) -- app_router.dart's `redirect` sends every
/// route here for that window. Deliberately bare (no data fetching, no
/// PageGlow) so it paints fast; matches web/index.html's pre-Flutter-boot
/// background color so there's no visible seam.
class SplashPage extends StatelessWidget {
  const SplashPage({super.key});

  @override
  Widget build(BuildContext context) {
    return const Scaffold(
      backgroundColor: AppColors.bg,
      body: Center(child: BrandMark(size: 48)),
    );
  }
}

import 'package:flutter/material.dart';

import '../../core/theme/app_colors.dart';
import '../../core/widgets/brand_mark.dart';

/// Shown ONLY while AuthState is AuthInitial (session restore/token
/// refresh still in flight) -- app_router.dart's `redirect` sends every
/// route here for that window instead of letting the originally-
/// requested route's real content build and briefly flash on screen
/// before bouncing to /login. Deliberately bare (no data fetching, no
/// PageGlow) so it paints as fast as possible; matches web/index.html's
/// own pre-Flutter-boot background color so there's no visible seam
/// between that and this.
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

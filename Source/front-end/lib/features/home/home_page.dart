import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/auth/auth_repository.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/brand_mark.dart';
import '../../core/widgets/page_glow.dart';

/// Placeholder for Phase 1 -- proves the auth-gated route actually works
/// end to end (a real login redirects here, logging out redirects back to
/// /login). The sport card grid (design/FRONTEND_STYLE.md's "Sport card"
/// component, driven by SportConfig/kSports) replaces this in Phase 5.
class HomePage extends ConsumerWidget {
  const HomePage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return Scaffold(
      backgroundColor: AppColors.bg,
      body: Stack(
        children: [
          const PageGlow(),
          SafeArea(
            child: Padding(
              padding: const EdgeInsets.all(24),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      const BrandMark(),
                      const SizedBox(width: 12),
                      Text('sports-predictor', style: AppTextStyles.sectionTitle()),
                      const Spacer(),
                      TextButton(
                        onPressed: () => ref.read(authRepositoryProvider.notifier).logout(),
                        child: Text('Sign out', style: AppTextStyles.body(color: AppColors.inkSub)),
                      ),
                    ],
                  ),
                  const SizedBox(height: 48),
                  Text('Signed in.', style: AppTextStyles.pageH1()),
                  const SizedBox(height: 12),
                  Text(
                    'Sport predictions land here once the events pipeline is wired up.',
                    style: AppTextStyles.body(),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}

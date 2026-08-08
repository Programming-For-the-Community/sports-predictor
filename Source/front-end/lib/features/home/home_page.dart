import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/auth/auth_repository.dart';
import '../../core/models/sport_config.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/brand_mark.dart';
import '../../core/widgets/page_glow.dart';
import '../../core/widgets/responsive.dart';
import '../../core/widgets/sport_card.dart';

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
            child: SingleChildScrollView(
              padding: const EdgeInsets.all(24),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      const BrandMark(),
                      const SizedBox(width: 12),
                      // Expanded, not a bare Text -- on a narrow (mobile)
                      // viewport the title needs to be able to give
                      // ground and ellipsize rather than push the sign-out
                      // button past the edge of the screen.
                      Expanded(
                        child: Text(
                          'sports-predictor',
                          style: AppTextStyles.sectionTitle(),
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                      TextButton(
                        onPressed: () => ref.read(authRepositoryProvider.notifier).logout(),
                        child: Text('Sign out', style: AppTextStyles.body(color: AppColors.inkSub)),
                      ),
                    ],
                  ),
                  const SizedBox(height: 40),
                  Text('Sports', style: AppTextStyles.pageH1()),
                  const SizedBox(height: 24),
                  LayoutBuilder(
                    builder: (context, constraints) {
                      final width = cardWidth(340, constraints.maxWidth);
                      return Wrap(
                        spacing: 20,
                        runSpacing: 20,
                        children: [
                          for (final sport in kSports)
                            SizedBox(width: width, child: SportCard(sport: sport)),
                        ],
                      );
                    },
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

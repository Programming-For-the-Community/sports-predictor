import 'dart:async';

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

class HomePage extends ConsumerStatefulWidget {
  const HomePage({super.key});

  @override
  ConsumerState<HomePage> createState() => _HomePageState();
}

class _HomePageState extends ConsumerState<HomePage> with WidgetsBindingObserver {
  Timer? _liveScoresTimer;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _liveScoresTimer = Timer.periodic(const Duration(seconds: 30), (_) => _refreshLiveScores());
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _liveScoresTimer?.cancel();
    super.dispose();
  }

  // Same "a backgrounded tab's own timers get throttled/paused, with
  // nothing catching back up on return" reasoning event_list_page.dart's
  // own didChangeAppLifecycleState carries in full -- every sport card's
  // LIVE dot is watched here for as long as this page stays open, with no
  // per-sport Events page ever mounted to run that page's own poll.
  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) _refreshLiveScores();
  }

  void _refreshLiveScores() {
    for (final sport in kSports) {
      invalidateLiveScoresFor(ref, sport);
    }
  }

  @override
  Widget build(BuildContext context) {
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
                      // Expanded so the title can give ground and
                      // ellipsize instead of pushing the sign-out button
                      // past the edge on a narrow (mobile) viewport.
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

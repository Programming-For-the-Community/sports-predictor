import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/auth/auth_repository.dart';
import '../../core/mobile/app_shell.dart';
import '../../core/mobile/app_updates.dart';
import '../../core/mobile/local_notifications.dart';
import '../../core/models/sport_config.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/brand_mark.dart';
import '../../core/widgets/page_glow.dart';
import '../../core/widgets/responsive.dart';
import '../../core/widgets/sport_card.dart';
import '../mobile/get_app_prompt.dart';
import '../mobile/update_banner.dart';

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
    // Asked here, once signed in, rather than over the login screen.
    if (ref.read(appShellProvider) == AppShell.androidApp) ref.read(deviceNotifierProvider).requestPermission();
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
    if (state == AppLifecycleState.resumed) {
      _refreshLiveScores();
      ref.invalidate(latestReleaseProvider);
    }
  }

  void _refreshLiveScores() {
    for (final sport in kSports) {
      invalidateLiveScoresFor(ref, sport);
    }
  }

  @override
  Widget build(BuildContext context) {
    final shell = ref.watch(appShellProvider);
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
                      // Expanded so the title can give ground and wrap
                      // instead of pushing the sign-out button past the
                      // edge on a narrow (mobile) viewport.
                      Expanded(
                        child: Text(
                          'sports-predictor',
                          style: AppTextStyles.sectionTitle(),
                        ),
                      ),
                      if (shell == AppShell.androidBrowser) ...[const GetAppButton(), const SizedBox(width: 4)],
                      // The Android app moves Sign out into Settings › Notifications.
                      if (shell == AppShell.androidApp)
                        const SettingsButton()
                      else
                        TextButton(
                          onPressed: () => ref.read(authRepositoryProvider.notifier).logout(),
                          child: Text('Sign out', style: AppTextStyles.body(color: AppColors.inkSub)),
                        ),
                    ],
                  ),
                  if (shell == AppShell.androidApp) ...[const SizedBox(height: 24), const UpdateBanner()],
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

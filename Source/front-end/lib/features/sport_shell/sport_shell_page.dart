import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../core/models/sport_config.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/page_glow.dart';

/// Per-sport tab shell: the "Events | Models" segmented toggle from
/// design/FRONTEND_STYLE.md's top-bar spec. Reads SportConfig from the
/// :sport path param for title/accent only -- no sport-specific logic
/// lives here.
enum _SportTab { events, season, models }

class SportShellPage extends StatelessWidget {
  const SportShellPage({super.key, required this.sportId, required this.child});

  final String sportId;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    final sport = sportById(sportId);
    final location = GoRouterState.of(context).matchedLocation;
    final activeTab = location.endsWith('/models')
        ? _SportTab.models
        : location.endsWith('/season')
            ? _SportTab.season
            : _SportTab.events;

    return Scaffold(
      backgroundColor: AppColors.bg,
      body: Stack(
        children: [
          const PageGlow(),
          SafeArea(
            child: Column(
              children: [
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 16),
                  child: Row(
                    children: [
                      IconButton(
                        onPressed: () => context.go('/'),
                        icon: const Icon(Icons.arrow_back, color: AppColors.inkSub),
                      ),
                      const SizedBox(width: 8),
                      Text(sport.displayName, style: AppTextStyles.sectionTitle()),
                      const Spacer(),
                      _TabToggle(sportId: sportId, activeTab: activeTab),
                    ],
                  ),
                ),
                Expanded(child: child),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _TabToggle extends StatelessWidget {
  const _TabToggle({required this.sportId, required this.activeTab});
  final String sportId;
  final _SportTab activeTab;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(4),
      decoration: BoxDecoration(color: AppColors.surface, borderRadius: BorderRadius.circular(999)),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          _TabButton(
            label: 'Events',
            active: activeTab == _SportTab.events,
            onTap: () => context.go('/$sportId/events'),
          ),
          _TabButton(
            label: 'Season',
            active: activeTab == _SportTab.season,
            onTap: () => context.go('/$sportId/season'),
          ),
          _TabButton(
            label: 'Models',
            active: activeTab == _SportTab.models,
            onTap: () => context.go('/$sportId/models'),
          ),
        ],
      ),
    );
  }
}

class _TabButton extends StatelessWidget {
  const _TabButton({required this.label, required this.active, required this.onTap});
  final String label;
  final bool active;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(999),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
        decoration: BoxDecoration(
          color: active ? AppColors.cyan : null,
          borderRadius: BorderRadius.circular(999),
        ),
        child: Text(
          label,
          style: AppTextStyles.body(color: active ? AppColors.bg : AppColors.inkSub).copyWith(fontWeight: FontWeight.w600),
        ),
      ),
    );
  }
}

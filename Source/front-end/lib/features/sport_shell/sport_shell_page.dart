import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../core/models/sport_config.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/page_glow.dart';

/// Per-sport tab shell: the "Events | Season | Models" segmented toggle
/// from design/FRONTEND_STYLE.md's top-bar spec. Reads SportConfig from the
/// :sport path param for title/accent and to gate the Season tab
/// (SportConfig.hasSeasonProjection) -- no other sport-specific logic
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

    // '/:sport/events/:eventId' has 4 segments (leading '' + sport +
    // 'events' + eventId); the events list itself only has 3. Only that
    // deeper, event-detail case should back out one level -- every other
    // tab (events list, season, models) is already top-level within this
    // shell, so '/' is still correct for it.
    final segments = location.split('/');
    final onEventDetail = segments.length > 3 && segments[2] == 'events';
    final backDestination = onEventDetail ? '/$sportId/events' : '/';

    return Scaffold(
      backgroundColor: AppColors.bg,
      body: Stack(
        children: [
          const PageGlow(),
          SafeArea(
            child: Column(
              children: [
                Padding(
                  padding: const EdgeInsets.fromLTRB(24, 16, 24, 0),
                  child: Row(
                    children: [
                      IconButton(
                        onPressed: () => context.go(backDestination),
                        icon: const Icon(Icons.arrow_back, color: AppColors.inkSub),
                      ),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text(
                          sport.displayName,
                          style: AppTextStyles.sectionTitle(),
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                    ],
                  ),
                ),
                // Own full-width row below the title -- on a phone-width
                // screen there's no longer a back button/title sharing
                // space with it, so the three tabs can stretch to fill
                // the row (see _TabToggle) instead of needing a
                // horizontal scroll to reach the third one.
                Padding(
                  padding: const EdgeInsets.fromLTRB(24, 12, 24, 4),
                  child: _TabToggle(sportId: sportId, activeTab: activeTab, showSeasonTab: sport.hasSeasonProjection),
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
  const _TabToggle({required this.sportId, required this.activeTab, required this.showSeasonTab});
  final String sportId;
  final _SportTab activeTab;
  final bool showSeasonTab;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(4),
      decoration: BoxDecoration(color: AppColors.surface, borderRadius: BorderRadius.circular(999)),
      child: Row(
        children: [
          Expanded(
            child: _TabButton(
              label: 'Events',
              active: activeTab == _SportTab.events,
              onTap: () => context.go('/$sportId/events'),
            ),
          ),
          if (showSeasonTab)
            Expanded(
              child: _TabButton(
                label: 'Season',
                active: activeTab == _SportTab.season,
                onTap: () => context.go('/$sportId/season'),
              ),
            ),
          Expanded(
            child: _TabButton(
              label: 'Models',
              active: activeTab == _SportTab.models,
              onTap: () => context.go('/$sportId/models'),
            ),
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
        alignment: Alignment.center,
        child: Text(
          label,
          textAlign: TextAlign.center,
          style: AppTextStyles.body(color: active ? AppColors.bg : AppColors.inkSub).copyWith(fontWeight: FontWeight.w600),
        ),
      ),
    );
  }
}

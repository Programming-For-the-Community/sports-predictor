import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/mobile/app_updates.dart';
import '../../core/routing/app_routes.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';

/// Home's "vX is ready" strip in the Android app. Hidden for a day after
/// "Later"; Settings keeps its dot regardless.
class UpdateBanner extends ConsumerWidget {
  const UpdateBanner({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final pending = ref.watch(pendingUpdateProvider).value;
    final snoozed = ref.watch(updateBannerSnoozedProvider).value ?? true;
    if (pending == null || snoozed) return const SizedBox.shrink();
    final installed = ref.watch(installedVersionProvider).value;

    return Container(
      padding: const EdgeInsets.fromLTRB(12, 8, 6, 8),
      decoration: BoxDecoration(
        color: AppColors.warn.withValues(alpha: 0.10),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: AppColors.warn.withValues(alpha: 0.35)),
      ),
      child: Row(
        children: [
          Container(width: 8, height: 8, decoration: const BoxDecoration(shape: BoxShape.circle, color: AppColors.warn)),
          const SizedBox(width: 10),
          Expanded(
            child: Text.rich(
              TextSpan(
                children: [
                  TextSpan(text: 'v${pending.versionName} is ready. ', style: AppTextStyles.body(color: AppColors.ink)),
                  if (installed != null) TextSpan(text: 'You\'re on v${installed.versionName}.', style: AppTextStyles.body(color: AppColors.inkSub)),
                ],
              ),
            ),
          ),
          TextButton(
            onPressed: () async {
              await ref.read(updateBannerSnoozeProvider).snooze();
              ref.invalidate(updateBannerSnoozedProvider);
            },
            child: Text('Later', style: AppTextStyles.body(color: AppColors.inkSub)),
          ),
          TextButton(
            onPressed: () => context.go(AppRoutes.appUpdates),
            child: Text('Update', style: AppTextStyles.body(color: AppColors.cyan).copyWith(fontWeight: FontWeight.w600)),
          ),
        ],
      ),
    );
  }
}

/// The header gear in the Android app, with a dot while an update is pending.
class SettingsButton extends ConsumerWidget {
  const SettingsButton({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final pending = ref.watch(pendingUpdateProvider).value != null;
    return IconButton(
      tooltip: 'Settings',
      onPressed: () => context.go(AppRoutes.settings),
      icon: Badge(
        isLabelVisible: pending,
        backgroundColor: AppColors.warn,
        smallSize: 8,
        child: const Icon(Icons.settings_outlined, color: AppColors.inkSub),
      ),
    );
  }
}

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../models/sport_config.dart';
import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';

/// design/FRONTEND_STYLE.md's "Sport card" component. Inactive (not yet
/// live on the backend) sport cards are non-interactive with a muted
/// "VIEW-ONLY"/"SOON" treatment.
class SportCard extends StatelessWidget {
  const SportCard({super.key, required this.sport});

  final SportConfig sport;

  @override
  Widget build(BuildContext context) {
    final active = sport.active;
    final accentStrip = sport.eventShape == EventShape.headToHead ? AppColors.accentStripH2h : AppColors.accentStripField;

    return Opacity(
      opacity: active ? 1 : 0.55,
      child: InkWell(
        onTap: active ? () => context.go('/${sport.id}/events') : null,
        borderRadius: BorderRadius.circular(18),
        child: Container(
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(18),
            gradient: LinearGradient(
              begin: Alignment.topLeft,
              end: Alignment.bottomRight,
              colors: AppColors.surfaceGrad,
            ),
            border: Border.all(color: AppColors.border),
          ),
          clipBehavior: Clip.antiAlias,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Container(height: 4, decoration: BoxDecoration(gradient: accentStrip)),
              Padding(
                padding: const EdgeInsets.all(26),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Container(
                          width: 8,
                          height: 8,
                          decoration: BoxDecoration(
                            shape: BoxShape.circle,
                            color: sport.accentColor,
                            boxShadow: [BoxShadow(color: sport.accentColor, blurRadius: 10)],
                          ),
                        ),
                        const SizedBox(width: 10),
                        // Expanded so the display name gives ground and
                        // ellipsizes on a narrow (mobile) card instead of
                        // overflowing past the status pill.
                        Expanded(
                          child: Text(
                            sport.displayName,
                            style: AppTextStyles.cardTitle(),
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                        const SizedBox(width: 8),
                        _StatusPill(active: active),
                      ],
                    ),
                    const SizedBox(height: 6),
                    Text(
                      sport.eventShape == EventShape.headToHead ? 'HEAD-TO-HEAD' : 'FIELD EVENT',
                      style: AppTextStyles.microLabel(),
                    ),
                    const SizedBox(height: 18),
                    Container(
                      padding: const EdgeInsets.all(14),
                      decoration: BoxDecoration(color: AppColors.inset, borderRadius: BorderRadius.circular(12)),
                      child: Row(
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        children: [
                          Flexible(
                            child: Text(
                              active ? 'View predictions' : 'Coming soon',
                              style: AppTextStyles.body(color: AppColors.inkSub),
                              overflow: TextOverflow.ellipsis,
                            ),
                          ),
                          if (active)
                            Icon(Icons.arrow_forward, size: 16, color: sport.accentColor)
                          else
                            Text('VIEW-ONLY', style: AppTextStyles.microLabel()),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _StatusPill extends StatelessWidget {
  const _StatusPill({required this.active});
  final bool active;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(
        color: active ? AppColors.live.withValues(alpha: 0.15) : AppColors.inset,
        borderRadius: BorderRadius.circular(999),
      ),
      child: Text(
        active ? 'LIVE' : 'SOON',
        style: AppTextStyles.microLabel(color: active ? AppColors.live : AppColors.inkMute),
      ),
    );
  }
}

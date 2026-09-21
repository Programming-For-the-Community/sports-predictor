import 'package:flutter/material.dart';

import '../../core/models/season_projection.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/responsive.dart';

// Shared NFL/NBA player-prop stat display labels.
const _statLabels = {
  'passing_yards': 'Passing Yards',
  'passing_touchdowns': 'Passing TDs',
  'rushing_yards': 'Rushing Yards',
  'rushing_touchdowns': 'Rushing TDs',
  'receiving_yards': 'Receiving Yards',
  'receiving_touchdowns': 'Receiving TDs',
  'defensive_sacks': 'Sacks',
  'points': 'Points',
  'rebounds': 'Rebounds',
  'assists': 'Assists',
  'steals': 'Steals',
  'blocks': 'Blocks',
  'three_pointers_made': '3-Pointers Made',
};

class SeasonLeaderboards extends StatelessWidget {
  const SeasonLeaderboards({super.key, required this.leaderboards});

  final Map<String, List<LeaderboardEntry>>? leaderboards;

  @override
  Widget build(BuildContext context) {
    final boards = leaderboards;
    if (boards == null) {
      return Text(
        'Leaderboards aren\'t available right now -- check back shortly.',
        style: AppTextStyles.body(color: AppColors.inkSub),
      );
    }
    final entries = [
      for (final entry in _statLabels.entries)
        if (boards[entry.key]?.isNotEmpty ?? false) entry,
    ];
    if (entries.isEmpty) {
      return Text('No leaderboard data yet.', style: AppTextStyles.body(color: AppColors.inkSub));
    }
    return LayoutBuilder(
      builder: (context, constraints) {
        final width = cardWidth(320, constraints.maxWidth);
        return Wrap(
          spacing: 20,
          runSpacing: 20,
          children: [
            for (final entry in entries)
              SizedBox(width: width, child: _LeaderboardCard(label: entry.value, entries: boards[entry.key]!)),
          ],
        );
      },
    );
  }
}

class _LeaderboardCard extends StatelessWidget {
  const _LeaderboardCard({required this.label, required this.entries});

  final String label;
  final List<LeaderboardEntry> entries;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(16),
        gradient: const LinearGradient(begin: Alignment.topLeft, end: Alignment.bottomRight, colors: AppColors.surfaceGrad),
        border: Border.all(color: AppColors.borderRaised),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label.toUpperCase(), style: AppTextStyles.microLabel(color: AppColors.cyan)),
          const SizedBox(height: 12),
          for (var i = 0; i < entries.length; i++)
            Padding(
              padding: EdgeInsets.only(top: i == 0 ? 0 : 8),
              child: Row(
                children: [
                  SizedBox(width: 20, child: Text('${i + 1}', style: AppTextStyles.microLabel())),
                  Expanded(
                    child: Text(
                      entries[i].displayName,
                      style: AppTextStyles.body(color: AppColors.ink),
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                  // Current total -> projected season-end total.
                  Text(
                    entries[i].currentTotal.toStringAsFixed(0),
                    style: AppTextStyles.metricValue(color: AppColors.inkMute),
                  ),
                  Text(' → ', style: AppTextStyles.microLabel(color: AppColors.inkMute)),
                  Text(
                    entries[i].projectedTotal.toStringAsFixed(0),
                    style: AppTextStyles.metricValue(color: AppColors.cyan),
                  ),
                ],
              ),
            ),
        ],
      ),
    );
  }
}

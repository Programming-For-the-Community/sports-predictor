import 'package:flutter/material.dart';

import '../models/event_leaders.dart';
import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';

/// Renders the `leaders` block once GET /{sport}/predictions/events/{id}
/// starts returning it (see event_leaders.dart) -- QB passing leader, top
/// 3 receivers, top 2 rushers, top 3 in sacks, per team.
class TeamLeadersPanel extends StatelessWidget {
  const TeamLeadersPanel({super.key, required this.homeAbbr, required this.awayAbbr, required this.leaders});

  final String homeAbbr;
  final String awayAbbr;
  final EventLeaders leaders;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(24),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(18),
        border: Border.all(color: AppColors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('PLAYER LEADERS', style: AppTextStyles.microLabel()),
          const SizedBox(height: 16),
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(child: _TeamLeadersColumn(label: homeAbbr, team: leaders.home)),
              const SizedBox(width: 24),
              Expanded(child: _TeamLeadersColumn(label: awayAbbr, team: leaders.away)),
            ],
          ),
        ],
      ),
    );
  }
}

class _TeamLeadersColumn extends StatelessWidget {
  const _TeamLeadersColumn({required this.label, required this.team});

  final String label;
  final TeamLeaders team;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label, style: AppTextStyles.cardTitle()),
        const SizedBox(height: 12),
        if (team.passing != null)
          _CategorySection(title: 'Passing', players: [team.passing!], statKeys: const ['passing_yards', 'passing_touchdowns']),
        if (team.rushing.isNotEmpty)
          _CategorySection(title: 'Rushing', players: team.rushing, statKeys: const ['rushing_yards', 'rushing_touchdowns']),
        if (team.receiving.isNotEmpty)
          _CategorySection(
              title: 'Receiving', players: team.receiving, statKeys: const ['receiving_yards', 'receiving_touchdowns']),
        if (team.sacks.isNotEmpty)
          _CategorySection(title: 'Sacks', players: team.sacks, statKeys: const ['defensive_sacks']),
      ],
    );
  }
}

class _CategorySection extends StatelessWidget {
  const _CategorySection({required this.title, required this.players, required this.statKeys});

  final String title;
  final List<PlayerStatLine> players;
  final List<String> statKeys;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(title.toUpperCase(), style: AppTextStyles.microLabel()),
          const SizedBox(height: 6),
          for (final player in players) _PlayerRow(player: player, statKeys: statKeys),
        ],
      ),
    );
  }
}

class _PlayerRow extends StatelessWidget {
  const _PlayerRow({required this.player, required this.statKeys});

  final PlayerStatLine player;
  final List<String> statKeys;

  static const _shortLabels = {
    'passing_yards': 'YDS',
    'rushing_yards': 'YDS',
    'receiving_yards': 'YDS',
    'passing_touchdowns': 'TD',
    'rushing_touchdowns': 'TD',
    'receiving_touchdowns': 'TD',
    'defensive_sacks': 'SACKS',
  };

  @override
  Widget build(BuildContext context) {
    final values = statKeys
        .map((key) {
          final value = player.stats[key];
          if (value == null) return null;
          return '${value.toStringAsFixed(0)} ${_shortLabels[key] ?? key.toUpperCase()}';
        })
        .whereType<String>()
        .join(' · ');

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        children: [
          Expanded(child: Text(player.displayName, style: AppTextStyles.body())),
          Text(values, style: AppTextStyles.metricValue(color: AppColors.cyan)),
        ],
      ),
    );
  }
}

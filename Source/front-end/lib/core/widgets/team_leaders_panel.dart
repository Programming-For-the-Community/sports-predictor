import 'package:flutter/material.dart';

import '../models/event_leaders.dart';
import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';

// Below this width, two team-leader columns side by side leave too little
// room per column for a player name and its stat values on one line --
// each Text squeezes down to single-character-wide wrapping instead of
// giving up first (confirmed: this is what "vertical" player names on
// mobile actually was). Stacked (one team's full column, then the
// other's) instead of side by side below this width.
const _stackBreakpoint = 560.0;

/// Away column then home column, side by side above _stackBreakpoint or
/// stacked full-width below it. Shared by TeamLeadersPanel and
/// TeamLeadersComparisonPanel -- same layout problem, same fix, for
/// upcoming and completed events respectively.
class _ResponsiveTeamColumns extends StatelessWidget {
  const _ResponsiveTeamColumns({required this.away, required this.home});

  final Widget away;
  final Widget home;

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        if (constraints.maxWidth < _stackBreakpoint) {
          return Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [away, const SizedBox(height: 20), home],
          );
        }
        return Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(child: away),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 12),
              child: Text('@', style: AppTextStyles.microLabel(color: AppColors.inkMute)),
            ),
            Expanded(child: home),
          ],
        );
      },
    );
  }
}

// Shared by _PlayerRow (predicted-only, upcoming events) and
// _ComparisonPlayerRow (predicted-vs-actual, completed events) below --
// same stat keys, same short display label either way.
const _statShortLabels = {
  'passing_yards': 'YDS',
  'rushing_yards': 'YDS',
  'receiving_yards': 'YDS',
  'passing_touchdowns': 'TD',
  'rushing_touchdowns': 'TD',
  'receiving_touchdowns': 'TD',
  'defensive_sacks': 'SACKS',
};

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
          // Away-then-home, same convention as matchup_hero.dart and
          // game_row.dart's _MatchupLine -- side by side or stacked, see
          // _ResponsiveTeamColumns.
          _ResponsiveTeamColumns(
            away: _TeamLeadersColumn(label: awayAbbr, team: leaders.away),
            home: _TeamLeadersColumn(label: homeAbbr, team: leaders.home),
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

  @override
  Widget build(BuildContext context) {
    final values = statKeys
        .map((key) {
          final value = player.stats[key];
          if (value == null) return null;
          return '${value.toStringAsFixed(0)} ${_statShortLabels[key] ?? key.toUpperCase()}';
        })
        .whereType<String>()
        .join(' · ');

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        children: [
          Expanded(
            child: Text(player.displayName, style: AppTextStyles.body(), maxLines: 1, overflow: TextOverflow.ellipsis),
          ),
          const SizedBox(width: 8),
          Flexible(
            child: Text(
              values,
              style: AppTextStyles.metricValue(color: AppColors.cyan),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              textAlign: TextAlign.end,
            ),
          ),
        ],
      ),
    );
  }
}

/// Completed-event counterpart to TeamLeadersPanel above -- same card
/// shape/away-@-home column layout, but shows predicted-vs-actual per
/// stat instead of a predicted-only value. Renders nothing (caller's job
/// to skip it, same "don't render if null" rule TeamLeadersPanel's own
/// caller already follows) when there's no comparison to show.
///
/// Also reused, unmodified, for a currently-live event's predicted-vs-
/// so-far comparison (see EventLeadersLiveComparison.toLiveComparison in
/// event_leaders.dart) -- `title` lets that caller swap in wording that
/// doesn't claim a final result.
class TeamLeadersComparisonPanel extends StatelessWidget {
  const TeamLeadersComparisonPanel({
    super.key,
    required this.homeAbbr,
    required this.awayAbbr,
    required this.comparison,
    this.title = 'PLAYER PROPS -- PREDICTED VS ACTUAL',
  });

  final String homeAbbr;
  final String awayAbbr;
  final EventLeadersComparison comparison;
  final String title;

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
          Text(title, style: AppTextStyles.microLabel()),
          const SizedBox(height: 16),
          _ResponsiveTeamColumns(
            away: _TeamLeadersComparisonColumn(label: awayAbbr, team: comparison.away),
            home: _TeamLeadersComparisonColumn(label: homeAbbr, team: comparison.home),
          ),
        ],
      ),
    );
  }
}

class _TeamLeadersComparisonColumn extends StatelessWidget {
  const _TeamLeadersComparisonColumn({required this.label, required this.team});

  final String label;
  final TeamLeadersComparison team;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label, style: AppTextStyles.cardTitle()),
        const SizedBox(height: 12),
        if (team.passing != null) _ComparisonCategorySection(title: 'Passing', players: [team.passing!]),
        if (team.rushing.isNotEmpty) _ComparisonCategorySection(title: 'Rushing', players: team.rushing),
        if (team.receiving.isNotEmpty) _ComparisonCategorySection(title: 'Receiving', players: team.receiving),
        if (team.sacks.isNotEmpty) _ComparisonCategorySection(title: 'Sacks', players: team.sacks),
      ],
    );
  }
}

class _ComparisonCategorySection extends StatelessWidget {
  const _ComparisonCategorySection({required this.title, required this.players});

  final String title;
  final List<PlayerStatLineComparison> players;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(title.toUpperCase(), style: AppTextStyles.microLabel()),
          const SizedBox(height: 6),
          for (final player in players) _ComparisonPlayerRow(player: player),
        ],
      ),
    );
  }
}

class _ComparisonPlayerRow extends StatelessWidget {
  const _ComparisonPlayerRow({required this.player});

  final PlayerStatLineComparison player;

  @override
  Widget build(BuildContext context) {
    // Predicted is what decides WHICH stats show -- actual is only ever
    // shown as a comparison against something that was predicted, never
    // a stat the player recorded that nobody predicted ahead of time
    // (that would be the different, not-built, "actual game leaders
    // regardless of prediction" feature).
    final segments = player.predicted.entries.map((entry) {
      final label = _statShortLabels[entry.key] ?? entry.key.toUpperCase();
      final predicted = entry.value.toStringAsFixed(0);
      final actual = player.actual[entry.key];
      final actualText = actual != null ? actual.toStringAsFixed(0) : '--';
      return '$actualText $label (pred $predicted)';
    }).join(' · ');

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        children: [
          Expanded(
            child: Text(player.displayName, style: AppTextStyles.body(), maxLines: 1, overflow: TextOverflow.ellipsis),
          ),
          const SizedBox(width: 8),
          Flexible(
            child: Text(
              segments,
              style: AppTextStyles.metricValue(color: AppColors.cyan),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              textAlign: TextAlign.end,
            ),
          ),
        ],
      ),
    );
  }
}

import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../../core/models/season_projection.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/team_color_dot.dart';
import '../../static/nfl_team_colors.dart';
import 'season_bracket_legend.dart';
import 'season_bracket_matchup_card.dart';

/// Below this width a table drops its title bar and shrinks its columns.
const _compactBreakpoint = 600.0;
const _wideTableWidth = 700.0;
const _gap = 24.0;

/// One round-odds card per conference or region, in [groupOrder] (any group
/// not named there follows), two to a row when there is room.
class RoundOddsTables extends StatelessWidget {
  const RoundOddsTables({
    super.key,
    required this.sport,
    required this.odds,
    required this.teamNames,
    this.groupOrder = const [],
  });

  final String sport;
  final RoundOdds odds;
  final Map<String, BracketTeamName> teamNames;
  final List<String> groupOrder;

  @override
  Widget build(BuildContext context) {
    final groups = [
      for (final group in groupOrder)
        if (odds.groups.containsKey(group)) group,
      for (final group in odds.groups.keys)
        if (!groupOrder.contains(group)) group,
    ];
    return LayoutBuilder(
      builder: (context, constraints) {
        final available = constraints.maxWidth;
        final compact = available < _compactBreakpoint;
        final twoUp = !compact && groups.length > 1 && available >= 2 * _compactBreakpoint + _gap;
        final width = math.min(_wideTableWidth, twoUp ? (available - _gap) / 2 : available);
        return Wrap(
          spacing: _gap,
          runSpacing: _gap,
          children: [
            for (final group in groups)
              SizedBox(
                width: width,
                child: _RoundOddsCard(
                  sport: sport,
                  group: group,
                  columns: odds.columns,
                  rows: odds.groups[group]!,
                  simulations: odds.simulations,
                  teamNames: teamNames,
                  compact: compact,
                ),
              ),
          ],
        );
      },
    );
  }
}

class _RoundOddsCard extends StatelessWidget {
  const _RoundOddsCard({
    required this.sport,
    required this.group,
    required this.columns,
    required this.rows,
    required this.simulations,
    required this.teamNames,
    required this.compact,
  });

  final String sport;
  final String group;
  final List<String> columns;
  final List<RoundOddsRow> rows;
  final int? simulations;
  final Map<String, BracketTeamName> teamNames;
  final bool compact;

  // The phone sizes follow the mock-up's compact table.
  TextStyle _headerStyle(Color color) {
    final style = AppTextStyles.microLabel(color: color);
    return compact ? style.copyWith(fontSize: 9, letterSpacing: 0.7) : style;
  }

  TextStyle get _teamStyle {
    final style = AppTextStyles.body(color: AppColors.ink);
    return compact ? style.copyWith(fontSize: 13) : style;
  }

  TextStyle _valueStyle(Color color) {
    final style = AppTextStyles.metricValue(color: color);
    return compact ? style.copyWith(fontSize: 12) : style;
  }

  @override
  Widget build(BuildContext context) {
    final simulations = this.simulations;
    return Container(
      padding: EdgeInsets.all(compact ? 16 : 20),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(14),
        gradient: const LinearGradient(begin: Alignment.topLeft, end: Alignment.bottomRight, colors: AppColors.surfaceGrad),
        border: Border.all(color: AppColors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(group.isEmpty ? 'Round odds' : 'Round odds · $group', style: AppTextStyles.sectionTitle()),
          if (simulations != null) ...[
            const SizedBox(height: 4),
            Text(
              'ACROSS ALL ${formatSimulationCount(simulations)} SIMULATIONS',
              style: AppTextStyles.microLabel(color: AppColors.inkSub),
            ),
          ],
          const SizedBox(height: 12),
          // Columns size to their content and share any spare width; a table
          // wider than the card (large text on a phone) scrolls sideways.
          LayoutBuilder(
            builder: (context, constraints) => SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: ConstrainedBox(constraints: BoxConstraints(minWidth: constraints.maxWidth), child: _table()),
            ),
          ),
        ],
      ),
    );
  }

  Widget _table() {
    final titleColumn = columns.length - 1;
    return Table(
      defaultVerticalAlignment: TableCellVerticalAlignment.middle,
      columnWidths: {
        0: const IntrinsicColumnWidth(),
        if (!compact) titleColumn + 1: const FixedColumnWidth(170),
      },
      defaultColumnWidth: const IntrinsicColumnWidth(flex: 1),
      children: [
        TableRow(children: [
          _header('TEAM', align: TextAlign.left),
          for (final (i, column) in columns.indexed)
            _header(
              // One word to a line on a phone, so a column is only as wide as its longest word.
              compact ? column.toUpperCase().replaceAll(' ', '\n') : column.toUpperCase(),
              color: compact && i == titleColumn ? AppColors.cyan : AppColors.inkSub,
            ),
        ]),
        for (final row in rows)
          TableRow(children: [
            _team(row),
            for (final (i, value) in row.values.indexed)
              if (!compact && i == titleColumn) _titleCell(value) else _value(value),
          ]),
      ],
    );
  }

  Widget _header(String label, {TextAlign align = TextAlign.right, Color color = AppColors.inkSub}) => Padding(
        padding: const EdgeInsets.only(left: 6, bottom: 8),
        child: Text(label, style: _headerStyle(color), textAlign: align),
      );

  Widget _team(RoundOddsRow row) {
    final info = teamDisplayFor(sport, row.teamId, teamNames[row.teamId]?.abbreviation, apiColor: teamNames[row.teamId]?.color);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 5),
      child: Row(
        children: [
          SizedBox(
            width: compact ? 16 : 20,
            child: Text(row.seed == null ? '' : '${row.seed}', style: AppTextStyles.microLabel(color: AppColors.inkSub)),
          ),
          TeamColorDot(color: info.primary),
          if (info.primary != null) const SizedBox(width: 6),
          Text(info.abbreviation, style: _teamStyle),
        ],
      ),
    );
  }

  Widget _value(double value, {Color color = AppColors.inkMid}) => Padding(
        padding: const EdgeInsets.only(left: 6),
        child: Text(formatRoundOdds(value), textAlign: TextAlign.right, style: _valueStyle(color)),
      );

  Widget _titleCell(double value) => Row(
        children: [
          const SizedBox(width: 12),
          Expanded(
            child: ClipRRect(
              borderRadius: BorderRadius.circular(3),
              child: SizedBox(
                height: 6,
                child: Stack(
                  children: [
                    const Positioned.fill(child: ColoredBox(color: AppColors.track)),
                    FractionallySizedBox(
                      alignment: Alignment.centerLeft,
                      widthFactor: value.clamp(0.0, 1.0),
                      heightFactor: 1,
                      child: const DecoratedBox(decoration: BoxDecoration(gradient: AppColors.cyanFill)),
                    ),
                  ],
                ),
              ),
            ),
          ),
          ConstrainedBox(constraints: const BoxConstraints(minWidth: 52), child: _value(value, color: AppColors.ink)),
        ],
      );
}

/// A team's chance of reaching a round; a dash once it can't.
String formatRoundOdds(double value) => value <= 0 ? '—' : formatBracketChance(value);

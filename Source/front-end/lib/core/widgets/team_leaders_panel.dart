import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../models/event_leaders.dart';
import '../models/sport_config.dart';
import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';
import 'pick_labels.dart';
import 'stat_value.dart';
import 'td_dots.dart';

// Below this width one team shows at a time, picked with a switch, and a
// comparison stacks each player's model row under their actual row.
const _stackBreakpoint = 560.0;

const _statShortLabels = {
  'passing_yards': 'YDS',
  'rushing_yards': 'YDS',
  'receiving_yards': 'YDS',
  'passing_touchdowns': 'TD',
  'rushing_touchdowns': 'TD',
  'receiving_touchdowns': 'TD',
  'defensive_sacks': 'SACKS',
  'points': 'PTS',
  'rebounds': 'REB',
  'assists': 'AST',
  'goals': 'G',
  'shots_total': 'SOG',
  'hits': 'HIT',
  'saves': 'SV',
};

String _statLabel(String key) => _statShortLabels[key] ?? key.toUpperCase();

class _CategoryConfig {
  const _CategoryConfig(this.key, this.title, this.statKeys);
  final String key;
  final String title;
  final List<String> statKeys;
}

// `passing`'s single starting-QB candidate is already a 0-or-1-element list
// (TeamLeaders.fromJson), so it needs no special case here.
const _footballCategories = [
  _CategoryConfig('passing', 'Passing', ['passing_yards', 'passing_touchdowns']),
  _CategoryConfig('rushing', 'Rushing', ['rushing_yards', 'rushing_touchdowns']),
  _CategoryConfig('receiving', 'Receiving', ['receiving_yards', 'receiving_touchdowns']),
  _CategoryConfig('sacks', 'Sacks', ['defensive_sacks']),
];

const _basketballCategories = [
  _CategoryConfig('scoring', 'Scoring', ['points']),
  _CategoryConfig('rebounding', 'Rebounding', ['rebounds']),
  _CategoryConfig('assists', 'Assists', ['assists']),
];

const _hockeyCategories = [
  _CategoryConfig('scoring', 'Scoring', ['goals', 'assists']),
  _CategoryConfig('shooting', 'Shooting', ['shots_total']),
  _CategoryConfig('physical', 'Physical', ['hits']),
  _CategoryConfig('goaltending', 'Goaltending', ['saves']),
];

bool _isFootball(String sport) => sport == SportIds.nfl || sport == SportIds.ncaafb;

List<_CategoryConfig> _categoriesFor(String sport) {
  if (sport == SportIds.nhl) return _hockeyCategories;
  return _isFootball(sport) ? _footballCategories : _basketballCategories;
}

/// "+18", "−3" or "0": how far the actual landed from the model.
String missText(String key, double actual, double predicted) {
  final gap = actual - predicted;
  final size = statValueText(key, gap.abs());
  if (size == '0') return '0';
  return '${gap > 0 ? '+' : '−'}$size';
}

BoxDecoration _panelDecoration() => BoxDecoration(
      color: AppColors.surface,
      borderRadius: BorderRadius.circular(18),
      border: Border.all(color: AppColors.border),
    );

/// The away and home columns side by side, each under its abbreviation; on a
/// narrow screen one at a time, chosen with a switch.
class _TeamColumns extends StatefulWidget {
  const _TeamColumns({required this.awayAbbr, required this.homeAbbr, required this.builder});

  final String awayAbbr;
  final String homeAbbr;
  // Builds one team's column; `stacked` is true on a narrow screen.
  final Widget Function(bool home, bool stacked) builder;

  @override
  State<_TeamColumns> createState() => _TeamColumnsState();
}

class _TeamColumnsState extends State<_TeamColumns> {
  bool _showHome = false;

  Widget _switchSide(String label, bool home) {
    final selected = _showHome == home;
    return Expanded(
      child: InkWell(
        onTap: () => setState(() => _showHome = home),
        child: Container(
          padding: const EdgeInsets.symmetric(vertical: 9),
          color: selected ? AppColors.borderRaised : null,
          alignment: Alignment.center,
          child: Text(label, style: AppTextStyles.microLabel(color: selected ? AppColors.ink : AppColors.inkSub)),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        if (constraints.maxWidth < _stackBreakpoint) {
          return Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              ClipRRect(
                borderRadius: BorderRadius.circular(999),
                child: DecoratedBox(
                  decoration: BoxDecoration(
                    border: Border.all(color: AppColors.border),
                    borderRadius: BorderRadius.circular(999),
                  ),
                  child: Row(children: [_switchSide(widget.awayAbbr, false), _switchSide(widget.homeAbbr, true)]),
                ),
              ),
              const SizedBox(height: 14),
              widget.builder(_showHome, true),
            ],
          );
        }
        Widget column(String abbr, bool home) => Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(abbr, style: AppTextStyles.cardTitle()),
                  const SizedBox(height: 12),
                  widget.builder(home, false),
                ],
              ),
            );
        return Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [column(widget.awayAbbr, false), const SizedBox(width: 28), column(widget.homeAbbr, true)],
        );
      },
    );
  }
}

/// Renders the `leaders` block from GET /{sport}/predictions/events/{id}:
/// per team, one table per category of the model's projected stat lines.
class TeamLeadersPanel extends StatelessWidget {
  const TeamLeadersPanel({super.key, required this.sport, required this.homeAbbr, required this.awayAbbr, required this.leaders});

  final String sport;
  final String homeAbbr;
  final String awayAbbr;
  final EventLeaders leaders;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(20),
      decoration: _panelDecoration(),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('PLAYER LEADERS', style: AppTextStyles.microLabel()),
          const SizedBox(height: 16),
          _TeamColumns(
            awayAbbr: awayAbbr,
            homeAbbr: homeAbbr,
            builder: (home, stacked) {
              final team = home ? leaders.home : leaders.away;
              return Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  for (final category in _categoriesFor(sport))
                    if (team[category.key].isNotEmpty)
                      _ProjectedSection(title: category.title, players: team[category.key], statKeys: category.statKeys),
                ],
              );
            },
          ),
        ],
      ),
    );
  }
}

/// A category's stat columns: the stats with a prediction to show, and each
/// dotted (touchdown or goal) column's dot-slot width.
class _StatColumns {
  _StatColumns(List<String> statKeys, List<Map<String, double>> predicted)
      : keys = statKeys.where((key) => predicted.any((stats) => stats[key] != null)).toList() {
    for (final key in keys) {
      final slots = tdDotSlots(key);
      if (slots != null) slotWidths[key] = _widestDots(key, slots, predicted) + _dotsGap;
    }
  }

  static const _columnGap = 14.0;
  static const _dotsGap = 5.0;

  final List<String> keys;
  final Map<String, double> slotWidths = {};

  /// Each dotted column's slot fits its widest prediction (a 3.2-TD passer
  /// needs four dots, not the usual two).
  static double _widestDots(String key, int slots, List<Map<String, double>> predicted) =>
      predicted.map((stats) => TdDots(value: stats[key] ?? 0, slots: slots).size.width).fold(0.0, math.max);

  /// A right-aligned cell. With [dots], every cell in a dotted column gets
  /// the same trailing slot -- the dots in a model cell, empty elsewhere.
  Widget cell(String key, Widget value, {bool dots = true, Widget? trailing}) {
    final slotWidth = dots ? slotWidths[key] : null;
    return Padding(
      padding: const EdgeInsets.only(left: _columnGap),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.end,
        children: [
          value,
          if (slotWidth != null) SizedBox(width: slotWidth, child: Align(alignment: Alignment.centerRight, child: trailing)),
        ],
      ),
    );
  }

  Widget label(String key, {bool dots = true}) => cell(key, Text(_statLabel(key), style: AppTextStyles.microLabel()), dots: dots);

  /// The model's value in cyan: a whole number plus dots for a dotted stat.
  Widget model(String key, double? predicted) {
    final style = AppTextStyles.metricValue(color: AppColors.cyan);
    if (predicted == null) return cell(key, Text('--', style: style));
    final slots = tdDotSlots(key);
    if (slots == null) return cell(key, Text(statValueText(key, predicted), style: style));
    return cell(
      key, Text('${predicted.round()}', style: style),
      trailing: TdDots(value: predicted, slots: slots, unit: tdDotUnit(key)),
    );
  }

  /// The real value in white, "--" before there is one.
  Widget actual(String key, double? value, {bool dots = true}) => cell(
        key,
        Text(value != null ? statValueText(key, value) : '--', style: AppTextStyles.metricValue(color: AppColors.ink)),
        dots: dots,
      );

  /// The gap for one stat, blank when either side of it is missing.
  Widget miss(String key, double? value, double? predicted) => Padding(
        padding: const EdgeInsets.only(left: _columnGap),
        child: Text(
          value == null || predicted == null ? '' : missText(key, value, predicted),
          style: AppTextStyles.metricValue(color: AppColors.inkSub), textAlign: TextAlign.end,
        ),
      );
}

Widget _titleCell(String title) =>
    Padding(padding: const EdgeInsets.only(bottom: 2), child: Text(title.toUpperCase(), style: AppTextStyles.microLabel()));

Widget _nameCell(String name) =>
    Padding(padding: const EdgeInsets.symmetric(vertical: 5), child: Text(name, style: AppTextStyles.body()));

const _rowDivider = BoxDecoration(border: Border(top: BorderSide(color: AppColors.border)));

const _missLabel = '±';

/// One category of projected stat lines: the stat names across the top, a
/// row per player.
class _ProjectedSection extends StatelessWidget {
  const _ProjectedSection({required this.title, required this.players, required this.statKeys});

  final String title;
  final List<PlayerStatLine> players;
  final List<String> statKeys;

  @override
  Widget build(BuildContext context) {
    final columns = _StatColumns(statKeys, [for (final player in players) player.stats]);
    return Padding(
      padding: const EdgeInsets.only(bottom: 14),
      child: Table(
        columnWidths: {0: const FlexColumnWidth(), for (var i = 1; i <= columns.keys.length; i++) i: const IntrinsicColumnWidth()},
        defaultVerticalAlignment: TableCellVerticalAlignment.middle,
        children: [
          TableRow(children: [_titleCell(title), for (final key in columns.keys) columns.label(key)]),
          for (final player in players)
            TableRow(
              decoration: _rowDivider,
              children: [_nameCell(player.displayName), for (final key in columns.keys) columns.model(key, player.stats[key])],
            ),
        ],
      ),
    );
  }
}

/// Predicted against actual per stat, for a finished game or -- fed the
/// live stat lines (see EventLeadersLiveComparison.toLiveComparison) -- one
/// in progress. [showMiss] adds the gap on each category's first stat; a
/// live caller turns it off, since a game in progress hasn't missed yet.
class TeamLeadersComparisonPanel extends StatelessWidget {
  const TeamLeadersComparisonPanel({
    super.key,
    required this.sport,
    required this.homeAbbr,
    required this.awayAbbr,
    required this.comparison,
    this.title = 'PLAYER PROPS',
    this.showMiss = true,
  });

  final String sport;
  final String homeAbbr;
  final String awayAbbr;
  final EventLeadersComparison comparison;
  final String title;
  final bool showMiss;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(20),
      decoration: _panelDecoration(),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Wrap(
            spacing: 16,
            runSpacing: 6,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [Text(title, style: AppTextStyles.microLabel()), const ActualModelKey()],
          ),
          const SizedBox(height: 16),
          _TeamColumns(
            awayAbbr: awayAbbr,
            homeAbbr: homeAbbr,
            builder: (home, stacked) {
              final team = home ? comparison.home : comparison.away;
              return Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  for (final category in _categoriesFor(sport))
                    if (team[category.key].isNotEmpty)
                      _ComparisonSection(
                        title: category.title, players: team[category.key], statKeys: category.statKeys,
                        stacked: stacked, showMiss: showMiss,
                      ),
                ],
              );
            },
          ),
        ],
      ),
    );
  }
}

/// One category of predicted-against-actual lines. Wide: each stat's actual
/// and model side by side. Stacked: each player's actual row (white) over
/// their model row (cyan). Either way the numbers sit in aligned columns and
/// a dotted column keeps a fixed slot for the model's dots.
class _ComparisonSection extends StatelessWidget {
  const _ComparisonSection({
    required this.title, required this.players, required this.statKeys, required this.stacked, required this.showMiss,
  });

  final String title;
  final List<PlayerStatLineComparison> players;
  final List<String> statKeys;
  final bool stacked;
  final bool showMiss;

  @override
  Widget build(BuildContext context) {
    // Predicted decides which stats show; actual is only ever a comparison
    // against something that was predicted.
    final columns = _StatColumns(statKeys, [for (final player in players) player.predicted]);
    if (columns.keys.isEmpty) return const SizedBox.shrink();
    final rows = stacked ? _stackedRows(columns) : _wideRows(columns);
    return Padding(
      padding: const EdgeInsets.only(bottom: 14),
      child: Table(
        columnWidths: {0: const FlexColumnWidth(), for (var i = 1; i < rows.first.children.length; i++) i: const IntrinsicColumnWidth()},
        defaultVerticalAlignment: TableCellVerticalAlignment.middle,
        children: rows,
      ),
    );
  }

  bool _hasMiss(_StatColumns columns, String key) => showMiss && key == columns.keys.first;

  Widget _missHeader() => Padding(
        padding: const EdgeInsets.only(left: _StatColumns._columnGap),
        child: Text(_missLabel, style: AppTextStyles.microLabel(), textAlign: TextAlign.end),
      );

  List<TableRow> _wideRows(_StatColumns columns) => [
        TableRow(children: [
          _titleCell(title),
          for (final key in columns.keys) ...[
            columns.label(key, dots: false),
            columns.cell(key, const SizedBox.shrink()),
            if (_hasMiss(columns, key)) _missHeader(),
          ],
        ]),
        for (final player in players)
          TableRow(decoration: _rowDivider, children: [
            _nameCell(player.displayName),
            for (final key in columns.keys) ...[
              columns.actual(key, player.actual[key], dots: false),
              columns.model(key, player.predicted[key]),
              if (_hasMiss(columns, key)) columns.miss(key, player.actual[key], player.predicted[key]),
            ],
          ]),
      ];

  List<TableRow> _stackedRows(_StatColumns columns) => [
        TableRow(children: [
          _titleCell(title),
          for (final key in columns.keys) ...[columns.label(key), if (_hasMiss(columns, key)) _missHeader()],
        ]),
        for (final (index, player) in players.indexed) ..._stackedPlayerRows(columns, player, divided: index > 0),
      ];

  /// The player's actual row in white, then the model's row in cyan under it.
  /// [divided] draws a hairline above the pair so the two read as one player.
  List<TableRow> _stackedPlayerRows(_StatColumns columns, PlayerStatLineComparison player, {required bool divided}) => [
        TableRow(decoration: divided ? _rowDivider : null, children: [
          Padding(padding: const EdgeInsets.only(top: 6), child: Text(player.displayName, style: AppTextStyles.body())),
          for (final key in columns.keys) ...[
            Padding(padding: const EdgeInsets.only(top: 6), child: columns.actual(key, player.actual[key])),
            if (_hasMiss(columns, key))
              Padding(
                padding: const EdgeInsets.only(top: 6),
                child: columns.miss(key, player.actual[key], player.predicted[key]),
              ),
          ],
        ]),
        TableRow(children: [
          const SizedBox.shrink(),
          for (final key in columns.keys) ...[
            Padding(padding: const EdgeInsets.only(bottom: 6), child: columns.model(key, player.predicted[key])),
            if (_hasMiss(columns, key)) const SizedBox.shrink(),
          ],
        ]),
      ];
}

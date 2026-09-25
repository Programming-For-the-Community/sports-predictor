import 'package:flutter/material.dart';

import '../../core/models/season_projection.dart';
import '../../core/models/sport_config.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/team_color_dot.dart';
import '../../static/nfl_team_colors.dart';
import '../../core/widgets/fit_text.dart';

// One list of (label, flex, cell) drives both the header row and every
// data row, keeping each column's label and value in sync per sport.
class _StandingsColumn {
  const _StandingsColumn(this.label, this.flex, this.cell);
  final String label;
  final int flex;
  final Widget Function(BuildContext context, String sport, TeamStanding team) cell;
}

// Every column header this table can show, across all 4 h2h sports --
// not shared with any other file (each sport-shaped table in this app
// owns its own column labels), but named here instead of typed inline in
// _standingsColumns below.
abstract final class _StandingsLabels {
  static const rank = 'RANK';
  static const team = 'TEAM';
  static const proj = 'PROJ';
  static const rec = 'REC';
  static const playIn = 'PLAY-IN%'; // NBA's own extra playoff-seeding tier
  static const conf = 'CONF%'; // NCAAFB/NCAA MBB
  static const div = 'DIV%'; // NFL
  static const playoffsNba = 'PLAYOFFS%';
  static const cfp = 'CFP%'; // NCAAFB
  static const ncaaTourney = 'NCAA%'; // NCAA MBB
  static const po = 'PO%'; // NFL
  static const champNbaNcaambb = 'CHAMP%'; // NBA/NCAA MBB
  static const nc = 'NC%'; // NCAAFB
  static const sb = 'SB%'; // NFL
}

String _playoffLabel(bool isNba, bool isNcaafb, bool isNcaambb) {
  if (isNba) return _StandingsLabels.playoffsNba;
  if (isNcaafb) return _StandingsLabels.cfp;
  if (isNcaambb) return _StandingsLabels.ncaaTourney;
  return _StandingsLabels.po;
}

String _championshipLabel(bool isNba, bool isNcaafb, bool isNcaambb) {
  if (isNba || isNcaambb) return _StandingsLabels.champNbaNcaambb;
  if (isNcaafb) return _StandingsLabels.nc;
  return _StandingsLabels.sb;
}

List<_StandingsColumn> _standingsColumns(String sport) {
  final isNcaafb = sport == SportIds.ncaafb;
  final isNba = sport == SportIds.nba;
  final isNcaambb = sport == SportIds.ncaambb;
  return [
    // NCAAFB/NCAA MBB only -- one column carries both the real rank and
    // the ranking model's own opinion (real -> model, same arrow
    // convention _LeaderboardCard uses for current -> projected), rather
    // than spending a whole extra column on the comparison.
    if (isNcaafb || isNcaambb)
      _StandingsColumn(_StandingsLabels.rank, 3, (context, sport, team) {
        final real = team.currentRank;
        final model = team.modelRank;
        return FittedBox(
          fit: BoxFit.scaleDown,
          child: RichText(
            textAlign: TextAlign.center,
            maxLines: 1,
            softWrap: false,
            text: TextSpan(
              children: [
                TextSpan(text: real != null ? '#$real' : '--', style: AppTextStyles.metricValue(color: AppColors.inkMute)),
                if (model != null) ...[
                  TextSpan(text: ' → ', style: AppTextStyles.microLabel(color: AppColors.inkMute)),
                  TextSpan(text: '#$model', style: AppTextStyles.metricValue(color: AppColors.cyan)),
                ],
              ],
            ),
          ),
        );
      }),
    _StandingsColumn(_StandingsLabels.team, 3, (context, sport, team) {
      final info = teamDisplayFor(sport, team.teamId, team.abbreviation, apiColor: team.color);
      return Row(
        children: [
          TeamColorDot(color: info.primary),
          if (info.primary != null) const SizedBox(width: 10),
          Flexible(
            child: Text(info.abbreviation, style: AppTextStyles.body(color: AppColors.ink)),
          ),
        ],
      );
    }),
    _StandingsColumn(_StandingsLabels.proj, 2, (context, sport, team) => Text(
          // Rounded to whole games -- projectedWins/Losses are Monte Carlo
          // averages, not a real final record.
          '${team.projectedWins.round()}-${team.projectedLosses.round()}',
          style: AppTextStyles.metricValue(color: AppColors.cyan),
          textAlign: TextAlign.center,
        )),
    _StandingsColumn(_StandingsLabels.rec, 2, (context, sport, team) => Text(
          // Ties only appended when non-zero (always 0 for NBA).
          team.ties > 0 ? '${team.wins}-${team.losses}-${team.ties}' : '${team.wins}-${team.losses}',
          style: AppTextStyles.metricValue(),
          textAlign: TextAlign.center,
        )),
    // NBA swaps DIV% for PLAY-IN%, its extra playoff-seeding tier.
    if (isNba)
      _StandingsColumn(_StandingsLabels.playIn, 2, (context, sport, team) => _PercentText(team.playInProbability ?? 0.0))
    else
      _StandingsColumn(
        isNcaafb || isNcaambb ? _StandingsLabels.conf : _StandingsLabels.div,
        2, (context, sport, team) => _PercentText(team.divisionWinnerProbability),
      ),
    _StandingsColumn(
      _playoffLabel(isNba, isNcaafb, isNcaambb),
      2, (context, sport, team) => _PercentText(team.playoffProbability),
    ),
    _StandingsColumn(
      _championshipLabel(isNba, isNcaafb, isNcaambb),
      2, (context, sport, team) => _PercentText(team.championshipProbability),
    ),
  ];
}

class StandingsTable extends StatelessWidget {
  const StandingsTable({super.key, required this.sport, required this.standings});

  final String sport;
  final List<TeamStanding> standings;

  @override
  Widget build(BuildContext context) {
    if (standings.isEmpty) {
      return Text('No standings available yet.', style: AppTextStyles.body(color: AppColors.inkSub));
    }
    final columns = _standingsColumns(sport);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 20),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(16),
        gradient: const LinearGradient(begin: Alignment.topLeft, end: Alignment.bottomRight, colors: AppColors.surfaceGrad),
        border: Border.all(color: AppColors.borderRaised),
      ),
      child: Column(
        children: [
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 10),
            child: _StandingsHeaderRow(columns: columns),
          ),
          for (final team in standings) ...[
            const Divider(height: 1, color: AppColors.border),
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 12),
              child: _StandingsRow(sport: sport, team: team, columns: columns),
            ),
          ],
        ],
      ),
    );
  }
}

class _StandingsHeaderRow extends StatelessWidget {
  const _StandingsHeaderRow({required this.columns});

  final List<_StandingsColumn> columns;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        for (var i = 0; i < columns.length; i++) ...[
          if (i > 0) const SizedBox(width: 6),
          Expanded(
            flex: columns[i].flex,
            child: FitText(
              columns[i].label,
              style: AppTextStyles.microLabel(),
              textAlign: i == 0 ? TextAlign.start : TextAlign.center
            ),
          ),
        ],
      ],
    );
  }
}

class _StandingsRow extends StatelessWidget {
  const _StandingsRow({required this.sport, required this.team, required this.columns});

  final String sport;
  final TeamStanding team;
  final List<_StandingsColumn> columns;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        for (final column in columns) Expanded(flex: column.flex, child: column.cell(context, sport, team)),
      ],
    );
  }
}

class _PercentText extends StatelessWidget {
  const _PercentText(this.value);

  final double value;

  @override
  Widget build(BuildContext context) {
    return Text(
      '${(value * 100).round()}%',
      style: AppTextStyles.metricValue(color: value >= 0.5 ? AppColors.cyan : AppColors.inkSub),
      textAlign: TextAlign.center,
    );
  }
}

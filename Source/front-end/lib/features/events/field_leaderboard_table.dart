import 'package:flutter/material.dart';

import '../../core/models/field_live_score.dart';
import '../../core/models/field_prediction.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/field_status_pill.dart';

/// PGA's own leaderboard table -- column-spec pattern, same shape as
/// season_page.dart's own _StandingsTable/_StandingsColumn (chosen over
/// the simpler _LeaderboardCard pattern there: a ~150-golfer field needs
/// 6+ compact columns, which is exactly what _StandingsColumn's
/// List<{label, flex, cell}> shape was built for). Public (not private
/// like _StandingsTable) since it's used from field_event_detail_page.dart,
/// a different file.
class _LeaderboardColumn {
  const _LeaderboardColumn(this.label, this.flex, this.cell);
  final String label;
  final int flex;
  final Widget Function(BuildContext context, FieldParticipantPrediction entry, FieldParticipantLiveResult? live) cell;
}

List<_LeaderboardColumn> _leaderboardColumns() => [
      _LeaderboardColumn('#', 1, (context, entry, live) {
        final position = live?.finishPosition ?? entry.actualFinishPosition;
        final isTie = live?.isTie ?? false;
        final label = position != null ? (isTie ? 'T$position' : '$position') : '--';
        return Text(
          label, style: AppTextStyles.metricValue(color: AppColors.inkMute), textAlign: TextAlign.center,
          maxLines: 1, softWrap: false, overflow: TextOverflow.ellipsis,
        );
      }),
      _LeaderboardColumn('PLAYER', 4, (context, entry, live) {
        final name = entry.name ?? entry.entityId;
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(name, style: AppTextStyles.body(color: AppColors.ink), maxLines: 1, overflow: TextOverflow.ellipsis),
            if (entry.country != null)
              Text(entry.country!, style: AppTextStyles.microLabel(color: AppColors.inkSub)),
          ],
        );
      }),
      // The status pill the user asked for -- one per golfer row, showing
      // their round status (scheduled/finished/cut/MDF/withdrawn/still
      // playing). Prefers the live overlay's status when present (fresher),
      // falls back to the prediction response's own actual-result absence
      // (no live overlay fetched yet, or this event isn't in a live window).
      _LeaderboardColumn('STATUS', 2, (context, entry, live) {
        final status = live?.status ?? (entry.actualFinishPosition != null ? 'finished' : null);
        return Center(child: FieldStatusPill(status: status));
      }),
      _LeaderboardColumn('TO PAR', 2, (context, entry, live) {
        final scoreToPar = live?.scoreToPar ?? entry.actualScoreToPar;
        return Text(
          _formatToPar(scoreToPar), style: AppTextStyles.metricValue(), textAlign: TextAlign.center,
        );
      }),
      _LeaderboardColumn('PROJ', 2, (context, entry, live) {
        final projected = entry.projectedScoreToPar?.value;
        return Text(
          projected != null ? _formatToPar(projected) : '--',
          style: AppTextStyles.metricValue(color: AppColors.cyan), textAlign: TextAlign.center,
        );
      }),
      _LeaderboardColumn('TOP 10%', 2, (context, entry, live) => _PercentText(entry.top10Probability?.value)),
      _LeaderboardColumn('TOP 5%', 2, (context, entry, live) => _PercentText(entry.top5Probability?.value)),
    ];

String _formatToPar(num? scoreToPar) {
  if (scoreToPar == null) return '--';
  final rounded = scoreToPar.round();
  if (rounded == 0) return 'E';
  return rounded > 0 ? '+$rounded' : '$rounded';
}

class FieldLeaderboardTable extends StatelessWidget {
  const FieldLeaderboardTable({super.key, required this.field, this.liveResults = const {}});

  final List<FieldParticipantPrediction> field;
  // Optional overlay from fieldLiveScoresProvider -- the table itself
  // doesn't own polling, the detail page feeds fresher data in as it
  // arrives (same split GameRow uses for its own isLive/liveState params).
  final Map<String, FieldParticipantLiveResult> liveResults;

  @override
  Widget build(BuildContext context) {
    if (field.isEmpty) {
      return Text('No field available yet.', style: AppTextStyles.body(color: AppColors.inkSub));
    }
    final columns = _leaderboardColumns();
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 20),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(16),
        gradient: LinearGradient(begin: Alignment.topLeft, end: Alignment.bottomRight, colors: AppColors.surfaceGrad),
        border: Border.all(color: AppColors.borderRaised),
      ),
      child: Column(
        children: [
          Padding(padding: const EdgeInsets.symmetric(vertical: 10), child: _LeaderboardHeaderRow(columns: columns)),
          for (final entry in field) ...[
            const Divider(height: 1, color: AppColors.border),
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 12),
              child: _LeaderboardRow(entry: entry, live: liveResults[entry.entityId], columns: columns),
            ),
          ],
        ],
      ),
    );
  }
}

class _LeaderboardHeaderRow extends StatelessWidget {
  const _LeaderboardHeaderRow({required this.columns});

  final List<_LeaderboardColumn> columns;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        for (var i = 0; i < columns.length; i++) ...[
          if (i > 0) const SizedBox(width: 6),
          Expanded(
            flex: columns[i].flex,
            child: Text(
              columns[i].label,
              style: AppTextStyles.microLabel(),
              textAlign: i == 0 ? TextAlign.start : TextAlign.center,
              maxLines: 1,
              softWrap: false,
              overflow: TextOverflow.ellipsis,
            ),
          ),
        ],
      ],
    );
  }
}

class _LeaderboardRow extends StatelessWidget {
  const _LeaderboardRow({required this.entry, required this.live, required this.columns});

  final FieldParticipantPrediction entry;
  final FieldParticipantLiveResult? live;
  final List<_LeaderboardColumn> columns;

  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.center,
      children: [
        for (final column in columns) Expanded(flex: column.flex, child: column.cell(context, entry, live)),
      ],
    );
  }
}

class _PercentText extends StatelessWidget {
  const _PercentText(this.value);

  final double? value;

  @override
  Widget build(BuildContext context) {
    if (value == null) {
      return Text('--', style: AppTextStyles.metricValue(color: AppColors.inkMute), textAlign: TextAlign.center);
    }
    return Text(
      '${(value! * 100).round()}%',
      style: AppTextStyles.metricValue(color: AppColors.violet),
      textAlign: TextAlign.center,
    );
  }
}

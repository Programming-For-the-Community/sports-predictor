import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../data/events_repository.dart';
import '../models/event.dart';
import '../models/event_status.dart';
import '../models/live_score.dart';
import '../models/prediction.dart';
import '../models/upset.dart';
import '../routing/app_routes.dart';
import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';
import '../../static/nfl_team_colors.dart';
import 'confidence_pill.dart';
import 'live_status_pill.dart';
import 'pick_labels.dart';
import 'possession_ball.dart';
import 'prediction_computing_retry.dart';
import 'prediction_freshness_badge.dart';
import 'team_color_dot.dart';
import 'win_probability_bar.dart';

// Below this width the row becomes a stacked card.
const _stackBreakpoint = 600.0;

// The backend's full round names don't fit the row's status slot.
const _roundAbbreviations = {
  'Wild Card': 'WC',
  'Divisional': 'DIV',
  'Conference Championship': 'CONF',
  'Super Bowl': 'SB',
};

String _weekLabel(SportEvent event) {
  final round = event.round;
  if (round != null) return _roundAbbreviations[round] ?? round;
  return event.week != null ? 'WK ${event.week}' : '';
}

/// "1:00 PM" for an already-local time.
String clockLabel(DateTime local) {
  final hour12 = local.hour % 12 == 0 ? 12 : local.hour % 12;
  final minute = local.minute.toString().padLeft(2, '0');
  final period = local.hour < 12 ? 'AM' : 'PM';
  return '$hour12:$minute $period';
}

/// "1:00 PM" in the viewer's local time -- '' without a kickoff time.
String _kickoffTimeLabel(SportEvent event) {
  final kickoff = event.kickoffTime;
  if (kickoff == null) return '';
  final local = DateTime.tryParse(kickoff)?.toLocal();
  return local == null ? '' : clockLabel(local);
}

const _usTimeZones = {
  -5: ('EST', 'EDT'),
  -6: ('CST', 'CDT'),
  -7: ('MST', 'MDT'),
  -8: ('PST', 'PDT'),
  -9: ('AKST', 'AKDT'),
  -10: ('HST', 'HST'),
};

/// The viewer's timezone, abbreviated (e.g. "EDT"). Derived from the
/// January/July offsets since DateTime.timeZoneName is unreliable on
/// Flutter Web; "UTC±N" outside the continental US/AK/HI.
String localTimezoneLabel() {
  final now = DateTime.now();
  return timezoneLabelFor(
    now.timeZoneOffset,
    DateTime(now.year, 1, 15).timeZoneOffset,
    DateTime(now.year, 7, 15).timeZoneOffset,
  );
}

/// localTimezoneLabel's logic, given the current, mid-January and mid-July
/// UTC offsets.
String timezoneLabelFor(Duration offset, Duration janOffset, Duration julOffset) {
  final standardOffset = janOffset <= julOffset ? janOffset : julOffset;
  final isDst = offset != standardOffset;

  final names = _usTimeZones[standardOffset.inHours];
  if (names != null) return isDst ? names.$2 : names.$1;

  final sign = offset.isNegative ? '-' : '+';
  return 'UTC$sign${offset.abs().inHours}';
}

T? _dataOrNull<T>(AsyncValue<T>? value) => value?.when(data: (v) => v, loading: () => null, error: (_, __) => null);

/// Everything one row shows, resolved once so the wide and stacked layouts
/// read the same values.
class _RowView {
  const _RowView({
    required this.sport,
    required this.event,
    required this.home,
    required this.away,
    required this.isCompleted,
    required this.isLive,
    required this.liveFinished,
    required this.liveDetail,
    required this.situation,
    required this.homeScore,
    required this.awayScore,
    required this.prediction,
  });

  final String sport;
  final SportEvent event;
  final NflTeam home;
  final NflTeam away;
  // This event's stored status. A game ESPN already reports over can still be
  // "scheduled" here for up to a day; that one is [liveFinished].
  final bool isCompleted;
  final bool isLive;
  final bool liveFinished;
  final String? liveDetail;
  final FootballSituation? situation;
  final double? homeScore;
  final double? awayScore;
  // Null for a completed event, which uses its logged comparison instead.
  final AsyncValue<EventPrediction>? prediction;

  PredictionComparison? get comparison => event.predictionComparison;
  EventPrediction? get predicted => _dataOrNull(prediction);
  bool get isFinal => isCompleted || liveFinished;

  double? get homeModel => predicted?.homeScore ?? (isCompleted ? comparison?.predictedHomeScore : null);
  double? get awayModel => predicted?.awayScore ?? (isCompleted ? comparison?.predictedAwayScore : null);

  double? get homeWinProbability =>
      predicted?.homeWinProbability ?? (isCompleted ? comparison?.predictedHomeWinProbability : null);

  /// True when [side] is behind on a scoreboard that has both scores.
  bool trails({required bool home}) {
    final mine = home ? homeScore : awayScore;
    final theirs = home ? awayScore : homeScore;
    return mine != null && theirs != null && mine < theirs;
  }

  String get finalLabel => isCompleted ? event.finalLabel : 'FINAL';

  /// UPSET once final, UPSET WATCH before then -- see upsetFlag.
  UpsetFlag? get upset {
    final home = homeScore;
    final away = awayScore;
    return upsetFlag(
      favorite: event.bettingFavorite,
      isFinal: isFinal,
      homeWon: home == null || away == null || home == away ? null : home > away,
      homeWinProbability: predicted?.homeWinProbability,
    );
  }

  void invalidate(WidgetRef ref, String sport, String eventId) =>
      ref.invalidate(eventPredictionProvider((sport: sport, eventId: eventId)));
}

/// design/FRONTEND_STYLE.md's "Game row (list)" component: one shape for an
/// upcoming, live and finished game. A not-yet-completed row fetches its own
/// prediction, so a slow one never blocks the rest of the list; a completed
/// row shows the prediction logged before the game.
class GameRow extends ConsumerWidget {
  const GameRow({super.key, required this.sport, required this.event, this.liveState});

  final String sport;
  final SportEvent event;
  // From liveScoresProvider; null for any game not near its kickoff.
  final LiveEventState? liveState;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final isCompleted = event.status == EventStatus.completed;
    final isLive = liveState?.live ?? false;
    final liveFinished = liveState?.completed ?? false;
    final onScoreboard = isLive || liveFinished;
    final view = _RowView(
      sport: sport,
      event: event,
      home: teamDisplay(sport, event.home),
      away: teamDisplay(sport, event.away),
      isCompleted: isCompleted,
      isLive: isLive,
      liveFinished: liveFinished,
      liveDetail: liveState?.detail,
      situation: isLive ? liveState?.situation : null,
      homeScore: onScoreboard ? liveState?.homeScore : event.home.result?.score,
      awayScore: onScoreboard ? liveState?.awayScore : event.away.result?.score,
      prediction: isCompleted ? null : ref.watch(eventPredictionProvider((sport: sport, eventId: event.eventId))),
    );

    return InkWell(
      onTap: () => context.go(AppRoutes.eventDetail(sport, event.eventId)),
      borderRadius: BorderRadius.circular(16),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 14),
        decoration: BoxDecoration(
          color: AppColors.surface,
          borderRadius: BorderRadius.circular(16),
          border: Border.all(color: AppColors.border),
        ),
        child: LayoutBuilder(
          builder: (context, constraints) =>
              constraints.maxWidth < _stackBreakpoint ? _StackedRow(view: view) : _WideRow(view: view),
        ),
      ),
    );
  }
}

/// When | teams and scores | win chance or margins | pick.
class _WideRow extends StatelessWidget {
  const _WideRow({required this.view});
  final _RowView view;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        SizedBox(width: 96, child: _When(view: view, stacked: false)),
        const SizedBox(width: 16),
        SizedBox(width: 200, child: _Teams(view: view, showWinChance: false)),
        const SizedBox(width: 24),
        Expanded(child: _Middle(view: view)),
        const SizedBox(width: 24),
        SizedBox(width: 150, child: _Pick(view: view)),
      ],
    );
  }
}

/// Status and confidence on top, the teams, then the bar and the line.
class _StackedRow extends StatelessWidget {
  const _StackedRow({required this.view});
  final _RowView view;

  static const _pillShare = 0.6;

  Widget? _badge() {
    final comparison = view.comparison;
    if (view.isCompleted) return comparison == null ? null : VerdictPill(correct: comparison.correct);
    final probability = view.homeWinProbability;
    return probability == null ? null : ConfidencePill(homeWinProbability: probability);
  }

  @override
  Widget build(BuildContext context) {
    final badge = _badge();
    final upset = view.upset;
    final pills = [if (upset != null) UpsetChip(flag: upset), if (badge != null) badge];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        // The pills wrap under each other rather than squeeze the LIVE pill
        // and clock off a narrow card.
        LayoutBuilder(
          builder: (context, constraints) => Row(
            children: [
              Expanded(child: _When(view: view, stacked: true)),
              if (pills.isNotEmpty) ...[
                const SizedBox(width: 8),
                ConstrainedBox(
                  constraints: BoxConstraints(maxWidth: constraints.maxWidth * _pillShare),
                  child: Wrap(alignment: WrapAlignment.end, spacing: 8, runSpacing: 6, children: pills),
                ),
              ],
            ],
          ),
        ),
        const SizedBox(height: 10),
        _Teams(view: view, showWinChance: true),
        const SizedBox(height: 10),
        _StackedFooter(view: view),
      ],
    );
  }
}

/// Kickoff time, the LIVE pill and game clock, or FINAL.
class _When extends StatelessWidget {
  const _When({required this.view, required this.stacked});
  final _RowView view;
  // One line on a phone card; two lines in the wide row's fixed column.
  final bool stacked;

  @override
  Widget build(BuildContext context) {
    final week = _weekLabel(view.event);
    final Widget lead;
    String? trail = week.isEmpty ? null : week;
    if (view.isLive) {
      lead = const LiveStatusPill();
      final clock = view.liveDetail;
      trail = clock == null || clock.isEmpty ? null : clock;
    } else if (view.isFinal) {
      lead = Text(view.finalLabel, style: AppTextStyles.microLabel(color: AppColors.inkMid));
    } else {
      lead = Text(_kickoffTimeLabel(view.event), style: AppTextStyles.metricValue());
    }
    final trailing = trail == null
        ? null
        : Text(trail, style: AppTextStyles.microLabel(color: view.isLive ? AppColors.inkMid : AppColors.inkMute));
    if (stacked) {
      return Wrap(
        spacing: 8,
        runSpacing: 4,
        crossAxisAlignment: WrapCrossAlignment.center,
        children: [lead, if (trailing != null) trailing],
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [lead, if (trailing != null) ...[const SizedBox(height: 4), trailing]],
    );
  }
}

double? _sideChance(double? homeChance, {required bool home}) {
  if (homeChance == null) return null;
  return home ? homeChance : 1 - homeChance;
}

/// The model's score, "--" when a row with a real score has none, else blank.
String _modelText(double? model, {required bool hasScore}) {
  if (model != null) return model.round().toString();
  return hasScore ? '--' : '';
}

/// A margin signed so that home winning is positive.
double _homePositive(double margin, {required bool homeWon}) => margin.abs() * (homeWon ? 1 : -1);

/// How far the model's margin landed from the final one, counting a pick on
/// the wrong side as the two margins added together. Null without a
/// predicted margin.
double? _marginMiss(PredictionComparison c) {
  final predicted = c.predictedMargin;
  if (predicted == null) return null;
  return (_homePositive(c.actualMargin, homeWon: c.actualHomeWon) - _homePositive(predicted, homeWon: c.predictedHomeWon)).abs();
}

/// A finished game's margins: the model's line, an arrow, the final line,
/// and how far apart they were.
class _FinalMargins extends StatelessWidget {
  const _FinalMargins({required this.view, required this.comparison});

  final _RowView view;
  final PredictionComparison comparison;

  static String missLabel(double miss) => 'OFF BY ${miss.toStringAsFixed(1)}';

  @override
  Widget build(BuildContext context) {
    final (model, actual) = _finalLines(view, comparison);
    final miss = _marginMiss(comparison);
    return Wrap(
      spacing: 10,
      runSpacing: 6,
      crossAxisAlignment: WrapCrossAlignment.center,
      children: [
        Text(model, style: AppTextStyles.metricValue(color: AppColors.cyan)),
        const Icon(Icons.arrow_forward, size: 14, color: AppColors.inkMute),
        Text(actual, style: AppTextStyles.metricValue(color: AppColors.ink)),
        if (miss != null)
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
            decoration: BoxDecoration(color: AppColors.inkSub.withValues(alpha: 0.14), borderRadius: BorderRadius.circular(999)),
            child: Text(missLabel(miss), style: AppTextStyles.microLabel(color: AppColors.inkSub)),
          ),
      ],
    );
  }
}

/// Away over home: colour, name, the real score in white and the model's in
/// cyan, each number in its own aligned slot.
class _Teams extends StatelessWidget {
  const _Teams({required this.view, required this.showWinChance});
  final _RowView view;
  final bool showWinChance;

  Widget _line({required bool home}) {
    final team = home ? view.home : view.away;
    final score = home ? view.homeScore : view.awayScore;
    final model = home ? view.homeModel : view.awayModel;
    final chance = _sideChance(view.homeWinProbability, home: home);
    final hasBall = home ? view.situation?.homeHasBall : view.situation?.awayHasBall;
    final dimmed = view.trails(home: home);
    final ink = dimmed ? AppColors.inkSub : AppColors.ink;
    // A finished game shows the chance on the picked side only.
    final showChance = showWinChance && chance != null && (!view.isCompleted || chance >= 0.5);
    return Row(
      children: [
        TeamColorDot(color: team.primary),
        const SizedBox(width: 8),
        Expanded(
          child: Align(
            alignment: Alignment.centerLeft,
            child: FittedBox(
              fit: BoxFit.scaleDown,
              alignment: Alignment.centerLeft,
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(team.abbreviation, style: AppTextStyles.body(color: ink), maxLines: 1),
                  if (hasBall == true) ...[const SizedBox(width: 6), const PossessionBall()],
                ],
              ),
            ),
          ),
        ),
        SlotNumber(score?.toStringAsFixed(0) ?? '', width: 40, style: AppTextStyles.metricValue(color: ink)),
        const SizedBox(width: 8),
        SlotNumber(
          _modelText(model, hasScore: score != null),
          width: 36, style: AppTextStyles.metricValue(color: AppColors.cyan),
        ),
        if (showWinChance) ...[
          const SizedBox(width: 8),
          SlotNumber(
            showChance ? '${(chance * 100).round()}%' : '',
            width: 40,
            style: AppTextStyles.microLabel(color: chance != null && chance >= 0.5 ? AppColors.cyan : AppColors.inkSub),
          ),
        ],
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [_line(home: false), const SizedBox(height: 6), _line(home: true)],
    );
  }
}

/// The favourite's line: "BUF -3.5".
String _pickLine(_RowView view, EventPrediction p) =>
    lineLabel(p.homeWinProbability >= 0.5 ? view.home.abbreviation : view.away.abbreviation, p.margin);

/// The logged pick's line and the final one, model first.
(String model, String actual) _finalLines(_RowView view, PredictionComparison c) {
  final pick = c.predictedHomeWon ? view.home.abbreviation : view.away.abbreviation;
  final winner = c.actualHomeWon ? view.home.abbreviation : view.away.abbreviation;
  final margin = c.predictedMargin;
  return (
    margin == null ? pick : lineLabel(pick, margin),
    c.actualMargin == 0 ? 'TIE' : lineLabel(winner, c.actualMargin, trim: true),
  );
}

Widget _predictionError(_RowView view, Object error) => error is PredictionComputingException
    ? PredictionComputingRetry(
        sport: view.sport, eventId: view.event.eventId, retryAfterSeconds: error.retryAfterSeconds, compact: true,
        invalidatePrediction: view.invalidate,
      )
    : Text('--', style: AppTextStyles.body(color: AppColors.inkMute));

Widget _noPrediction() => Text('No prediction recorded', style: AppTextStyles.body(color: AppColors.inkMute));

/// The wide row's centre: each side's win chance over the bar before and
/// during a game, the model's line and the final one after it.
class _Middle extends StatelessWidget {
  const _Middle({required this.view});
  final _RowView view;

  Widget _ends(String left, Color leftColor, String right, Color rightColor) => Row(
        children: [
          Expanded(child: Text(left, style: AppTextStyles.microLabel(color: leftColor), maxLines: 1, softWrap: false)),
          const SizedBox(width: 8),
          Text(right, style: AppTextStyles.microLabel(color: rightColor), maxLines: 1, softWrap: false),
        ],
      );

  Widget _winChance(double homeWinProbability) {
    final homeFavored = homeWinProbability >= 0.5;
    final awayPercent = ((1 - homeWinProbability) * 100).round();
    final homePercent = (homeWinProbability * 100).round();
    final situation = view.situation;
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _ends(
          '${view.away.abbreviation} $awayPercent%', homeFavored ? AppColors.inkSub : AppColors.cyan,
          '${view.home.abbreviation} $homePercent%', homeFavored ? AppColors.cyan : AppColors.inkSub,
        ),
        const SizedBox(height: 6),
        WinProbabilityBar(homeWinProbability: homeWinProbability, height: 8),
        if (situation != null && situation.hasDown) ...[
          const SizedBox(height: 6),
          FootballSituationText(situation: situation),
        ],
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    if (view.isCompleted) {
      final comparison = view.comparison;
      return comparison == null ? _noPrediction() : _FinalMargins(view: view, comparison: comparison);
    }
    return view.prediction!.when(
      data: (p) => _winChance(p.homeWinProbability),
      loading: () => const WinProbabilityBar(homeWinProbability: 0.5, height: 8),
      error: (error, _) => _predictionError(view, error),
    );
  }
}

/// The wide row's right edge: the line and confidence, or the verdict.
class _Pick extends StatelessWidget {
  const _Pick({required this.view});
  final _RowView view;

  Widget _withUpset(Widget pill) {
    final upset = view.upset;
    if (upset == null) return pill;
    return Wrap(
      alignment: WrapAlignment.end,
      spacing: 6,
      runSpacing: 6,
      children: [pill, UpsetChip(flag: upset)],
    );
  }

  @override
  Widget build(BuildContext context) {
    final comparison = view.comparison;
    final List<Widget> children;
    if (view.isCompleted) {
      if (comparison == null) return const SizedBox.shrink();
      final pick = comparison.predictedHomeWon ? view.home.abbreviation : view.away.abbreviation;
      final chance = comparison.predictedHomeWon
          ? comparison.predictedHomeWinProbability
          : 1 - comparison.predictedHomeWinProbability;
      children = [
        _withUpset(VerdictPill(correct: comparison.correct)),
        const SizedBox(height: 6),
        Text('$pick ${(chance * 100).round()}%', style: AppTextStyles.microLabel(color: AppColors.cyan)),
      ];
    } else {
      final p = view.predicted;
      if (p == null) return const SizedBox.shrink();
      children = [
        FittedBox(
          fit: BoxFit.scaleDown,
          child: Text(_pickLine(view, p), style: AppTextStyles.metricValue(color: AppColors.cyan), maxLines: 1),
        ),
        const SizedBox(height: 6),
        _withUpset(ConfidencePill(homeWinProbability: p.homeWinProbability)),
        PredictionFreshnessBadge(
          sport: view.sport, eventId: view.event.eventId,
          stale: p.stale, retryAfterSeconds: p.staleRetryAfterSeconds, compact: true,
          invalidatePrediction: view.invalidate,
        ),
      ];
    }
    return Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.end, children: children);
  }
}

/// A phone card's last lines: the bar and the line before a game, the down
/// and the line during one, the model's line and the final one after it.
class _StackedFooter extends StatelessWidget {
  const _StackedFooter({required this.view});
  final _RowView view;

  Widget _line(String text, Color color) => Text(text, style: AppTextStyles.metricValue(color: color));

  Widget _upcoming(EventPrediction p) {
    final situation = view.situation;
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (!view.isLive && !view.isFinal) ...[
          WinProbabilityBar(homeWinProbability: p.homeWinProbability, height: 5),
          const SizedBox(height: 8),
        ],
        Wrap(
          alignment: WrapAlignment.spaceBetween,
          crossAxisAlignment: WrapCrossAlignment.center,
          spacing: 12,
          runSpacing: 4,
          children: [
            if (situation != null && situation.hasDown) FootballSituationText(situation: situation),
            _line(_pickLine(view, p), AppColors.cyan),
          ],
        ),
        PredictionFreshnessBadge(
          sport: view.sport, eventId: view.event.eventId,
          stale: p.stale, retryAfterSeconds: p.staleRetryAfterSeconds, compact: true,
          invalidatePrediction: view.invalidate,
        ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    if (view.isCompleted) {
      final comparison = view.comparison;
      return comparison == null ? _noPrediction() : _FinalMargins(view: view, comparison: comparison);
    }
    return view.prediction!.when(
      data: _upcoming,
      loading: () => const WinProbabilityBar(homeWinProbability: 0.5, height: 5),
      error: (error, _) => _predictionError(view, error),
    );
  }
}

import 'package:flutter/material.dart';

import '../models/event.dart';
import '../models/live_score.dart';
import '../models/prediction.dart';
import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';
import '../../static/nfl_team_colors.dart';
import 'confidence_pill.dart';
import 'final_status_pill.dart';
import 'game_row.dart' show clockLabel, localTimezoneLabel;
import 'live_status_pill.dart';
import 'pick_labels.dart';
import 'possession_ball.dart';
import 'team_color_dot.dart';
import 'win_probability_bar.dart';

const _weekdays = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN'];
const _months = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'];

/// "SAT, OCT 10" in the viewer's local time, plus " · 7:00 PM EDT" when
/// [withTime] and the event has a kickoff time.
String _dateLine(SportEvent event, {required bool withTime}) {
  final local = DateTime.tryParse(event.kickoffTime ?? event.eventDate)?.toLocal();
  if (local == null) return '';
  final day = '${_weekdays[local.weekday - 1]}, ${_months[local.month - 1]} ${local.day}';
  if (!withTime || event.kickoffTime == null) return day;
  return '$day · ${clockLabel(local)} ${localTimezoneLabel()}';
}

/// White for the side ahead (or level), dimmed for the one behind.
Color _scoreColor(double score, double? other) => other != null && score < other ? AppColors.inkSub : AppColors.ink;

Color _chanceColor(double chance) => chance >= 0.5 ? AppColors.cyan : AppColors.inkSub;

/// Away on the left of "@", home on the right.
Widget _sides(Widget away, Widget home) => Row(
      children: [
        Expanded(child: away),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12),
          child: Text('@', style: AppTextStyles.sectionTitle(color: AppColors.inkMute)),
        ),
        Expanded(child: home),
      ],
    );

BoxDecoration _heroDecoration() => BoxDecoration(
      borderRadius: BorderRadius.circular(20),
      gradient: const LinearGradient(begin: Alignment.topLeft, end: Alignment.bottomRight, colors: AppColors.surfaceGrad),
      border: Border.all(color: AppColors.borderRaised),
    );

/// design/FRONTEND_STYLE.md's "Matchup hero (detail)" component for a game
/// not yet stored as completed. Before kickoff each side's win chance is the
/// big number with the model's score under it; once [liveState] is live or
/// finished the real score takes the big slot and the model's score and win
/// chance sit under it in cyan. The bar, the line, the total and confidence
/// describe the pre-game pick and stay put throughout.
class MatchupHero extends StatelessWidget {
  const MatchupHero({super.key, required this.sport, required this.event, required this.prediction, this.liveState});

  final String sport;
  final SportEvent event;
  final EventPrediction prediction;
  final LiveEventState? liveState;

  @override
  Widget build(BuildContext context) {
    final home = teamDisplay(sport, event.home);
    final away = teamDisplay(sport, event.away);
    final homeChance = prediction.homeWinProbability;
    final homeFavored = homeChance >= 0.5;
    final state = liveState;
    final onScoreboard = state != null && (state.live || state.completed);
    final situation = state != null && state.live ? state.situation : null;
    final homeScore = onScoreboard ? state.homeScore : null;
    final awayScore = onScoreboard ? state.awayScore : null;

    Widget side({required bool isHome}) {
      final team = isHome ? home : away;
      final chance = isHome ? homeChance : 1 - homeChance;
      final model = (isHome ? prediction.homeScore : prediction.awayScore).round();
      final score = isHome ? homeScore : awayScore;
      final other = isHome ? awayScore : homeScore;
      final percent = '${(chance * 100).round()}%';
      return _Side(
        team: team,
        hasBall: (isHome ? situation?.homeHasBall : situation?.awayHasBall) ?? false,
        big: score != null ? score.toStringAsFixed(0) : percent,
        bigColor: score != null ? _scoreColor(score, other) : _chanceColor(chance),
        model: score != null ? '$model · $percent' : '$model',
      );
    }

    return Container(
      padding: const EdgeInsets.all(22),
      decoration: _heroDecoration(),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _Status(event: event, liveState: state),
          const SizedBox(height: 14),
          _sides(side(isHome: false), side(isHome: true)),
          const SizedBox(height: 16),
          WinProbabilityBar(homeWinProbability: homeChance, height: 8),
          const SizedBox(height: 16),
          const Divider(height: 1, thickness: 1, color: AppColors.border),
          const SizedBox(height: 14),
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: _Figure(
                  label: 'LINE',
                  lines: [(lineLabel(homeFavored ? home.abbreviation : away.abbreviation, prediction.margin), AppColors.cyan)],
                ),
              ),
              Expanded(
                child: _Figure(
                  label: 'TOTAL',
                  lines: [((prediction.homeScore + prediction.awayScore).toStringAsFixed(1), AppColors.cyan)],
                ),
              ),
              Expanded(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text('CONFIDENCE', style: AppTextStyles.microLabel(), textAlign: TextAlign.center),
                    const SizedBox(height: 6),
                    ConfidencePill(homeWinProbability: homeChance),
                  ],
                ),
              ),
            ],
          ),
          _StartingGoalies(
            goalies: prediction.goalies.isNotEmpty ? prediction.goalies : event.goalies,
            homeAbbr: home.abbreviation, awayAbbr: away.abbreviation,
          ),
        ],
      ),
    );
  }
}

/// The date, time and venue before a game; the LIVE pill, clock and down
/// during one; FINAL once ESPN reports it over.
class _Status extends StatelessWidget {
  const _Status({required this.event, required this.liveState});

  final SportEvent event;
  final LiveEventState? liveState;

  @override
  Widget build(BuildContext context) {
    final state = liveState;
    final venue = event.venueLabel;
    final venueLine = venue == null ? null : _VenueLabel(label: venue);
    if (state == null || !(state.live || state.completed)) {
      return Column(
        children: [
          Text(_dateLine(event, withTime: true), style: AppTextStyles.microLabel(color: AppColors.inkSub), textAlign: TextAlign.center),
          if (venueLine != null) ...[const SizedBox(height: 6), venueLine],
        ],
      );
    }
    final clock = state.live ? state.detail : null;
    final situation = state.live ? state.situation : null;
    return Column(
      children: [
        Wrap(
          alignment: WrapAlignment.center,
          crossAxisAlignment: WrapCrossAlignment.center,
          spacing: 8,
          runSpacing: 4,
          children: [
            state.live ? const LiveStatusPill() : const FinalStatusPill(),
            if (clock != null && clock.isNotEmpty) Text(clock, style: AppTextStyles.body(color: AppColors.inkSub)),
          ],
        ),
        if (situation != null && situation.hasDown) ...[
          const SizedBox(height: 6),
          FootballSituationText(situation: situation, separator: ' at ', textAlign: TextAlign.center),
        ],
        if (venueLine != null) ...[const SizedBox(height: 6), venueLine],
      ],
    );
  }
}

/// One team: colour and name, a big number, and the model's figures in cyan.
class _Side extends StatelessWidget {
  const _Side({
    required this.team, required this.big, required this.bigColor, required this.model, this.hasBall = false,
    this.dimmed = false,
  });

  final NflTeam team;
  final String big;
  final Color bigColor;
  // Null when no prediction was logged.
  final String? model;
  final bool hasBall;
  final bool dimmed;

  @override
  Widget build(BuildContext context) {
    final model = this.model;
    return Column(
      children: [
        FittedBox(
          fit: BoxFit.scaleDown,
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              TeamColorDot(color: team.primary, size: 10),
              const SizedBox(width: 8),
              Text(team.abbreviation, style: AppTextStyles.cardTitle(color: dimmed ? AppColors.inkSub : AppColors.ink)),
              if (hasBall) ...[const SizedBox(width: 8), const PossessionBall()],
            ],
          ),
        ),
        const SizedBox(height: 4),
        FittedBox(fit: BoxFit.scaleDown, child: Text(big, style: AppTextStyles.bigStatNumeral(color: bigColor))),
        if (model != null) ...[
          const SizedBox(height: 2),
          FittedBox(fit: BoxFit.scaleDown, child: Text(model, style: AppTextStyles.metricValue(color: AppColors.cyan))),
        ],
      ],
    );
  }
}

/// A labelled figure of one or two coloured lines (the actual in white over
/// the model in cyan, or the model alone).
class _Figure extends StatelessWidget {
  const _Figure({required this.label, required this.lines});

  final String label;
  final List<(String, Color)> lines;

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Text(label, style: AppTextStyles.microLabel(), textAlign: TextAlign.center),
        const SizedBox(height: 4),
        for (final (text, color) in lines)
          FittedBox(fit: BoxFit.scaleDown, child: Text(text, style: AppTextStyles.metricValueLarge(color: color))),
      ],
    );
  }
}

/// Each side's starting goalie and how sure the listing is, away then
/// home. Renders nothing for a sport or event with no goalies.
class _StartingGoalies extends StatelessWidget {
  const _StartingGoalies({required this.goalies, required this.homeAbbr, required this.awayAbbr});

  final Map<String, EventGoalie> goalies;
  final String homeAbbr;
  final String awayAbbr;

  Widget _side(String abbr, EventGoalie? goalie) {
    if (goalie == null) return const SizedBox.shrink();
    final tag = goalie.tag;
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Text('$abbr GOALIE', style: AppTextStyles.microLabel(), textAlign: TextAlign.center),
        const SizedBox(height: 4),
        Text(goalie.displayName, style: AppTextStyles.body(), textAlign: TextAlign.center),
        if (tag != null) ...[
          const SizedBox(height: 4),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(999),
              border: Border.all(color: AppColors.borderRaised),
            ),
            child: Text(tag.toUpperCase(), style: AppTextStyles.microLabel(color: AppColors.inkSub)),
          ),
        ],
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    if (goalies.isEmpty) return const SizedBox.shrink();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const SizedBox(height: 16),
        const Divider(height: 1, thickness: 1, color: AppColors.border),
        const SizedBox(height: 14),
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(child: _side(awayAbbr, goalies['away'])),
            const SizedBox(width: 12),
            Expanded(child: _side(homeAbbr, goalies['home'])),
          ],
        ),
      ],
    );
  }
}

/// MatchupHero for a completed game: the final score with the model's under
/// it, the verdict on the pick logged before the game, then the final line
/// and total over the model's. [comparison] is null when no prediction was
/// ever logged, not a loading state.
class MatchupResultHero extends StatelessWidget {
  const MatchupResultHero({super.key, required this.sport, required this.event, required this.comparison});

  final String sport;
  final SportEvent event;
  final PredictionComparison? comparison;

  static const noPredictionLabel = 'No prediction was recorded for this game.';

  @override
  Widget build(BuildContext context) {
    final home = teamDisplay(sport, event.home);
    final away = teamDisplay(sport, event.away);
    final homeWon = event.home.result?.won ?? false;
    final c = comparison;
    final venue = event.venueLabel;

    Widget side({required bool isHome}) {
      final score = (isHome ? event.home : event.away).result?.score;
      final model = isHome ? c?.predictedHomeScore : c?.predictedAwayScore;
      final won = isHome ? homeWon : !homeWon;
      return _Side(
        team: isHome ? home : away,
        big: score?.toStringAsFixed(0) ?? '--',
        bigColor: won ? AppColors.ink : AppColors.inkSub,
        model: model?.round().toString(),
        dimmed: !won,
      );
    }

    return Container(
      padding: const EdgeInsets.all(22),
      decoration: _heroDecoration(),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(
            [event.finalLabel, _dateLine(event, withTime: false)].where((part) => part.isNotEmpty).join(' · '),
            style: AppTextStyles.microLabel(color: AppColors.inkSub), textAlign: TextAlign.center,
          ),
          if (venue != null) ...[const SizedBox(height: 6), _VenueLabel(label: venue)],
          const SizedBox(height: 14),
          _sides(side(isHome: false), side(isHome: true)),
          const SizedBox(height: 16),
          if (c == null)
            Center(child: Text(noPredictionLabel, style: AppTextStyles.body(color: AppColors.inkMute), textAlign: TextAlign.center))
          else
            _Recap(comparison: c, homeAbbr: home.abbreviation, awayAbbr: away.abbreviation),
        ],
      ),
    );
  }
}

/// HIT or MISS beside the pick and its win chance, then the final line and
/// total in white over the model's in cyan.
class _Recap extends StatelessWidget {
  const _Recap({required this.comparison, required this.homeAbbr, required this.awayAbbr});

  final PredictionComparison comparison;
  final String homeAbbr;
  final String awayAbbr;

  @override
  Widget build(BuildContext context) {
    final c = comparison;
    final pick = c.predictedHomeWon ? homeAbbr : awayAbbr;
    final winner = c.actualHomeWon ? homeAbbr : awayAbbr;
    final chance = c.predictedHomeWon ? c.predictedHomeWinProbability : 1 - c.predictedHomeWinProbability;
    final margin = c.predictedMargin;
    final modelHome = c.predictedHomeScore;
    final modelAway = c.predictedAwayScore;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Wrap(
          alignment: WrapAlignment.center,
          crossAxisAlignment: WrapCrossAlignment.center,
          spacing: 10,
          runSpacing: 6,
          children: [
            VerdictPill(correct: c.correct),
            Text('$pick ${(chance * 100).round()}%', style: AppTextStyles.metricValue(color: AppColors.cyan)),
          ],
        ),
        const SizedBox(height: 16),
        const Divider(height: 1, thickness: 1, color: AppColors.border),
        const SizedBox(height: 14),
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(
              child: _Figure(
                label: 'LINE',
                lines: [
                  (c.actualMargin == 0 ? 'TIE' : lineLabel(winner, c.actualMargin, trim: true), AppColors.ink),
                  if (margin != null) (lineLabel(pick, margin), AppColors.cyan),
                ],
              ),
            ),
            Expanded(
              child: _Figure(
                label: 'TOTAL',
                lines: [
                  ((c.actualHomeScore + c.actualAwayScore).toStringAsFixed(0), AppColors.ink),
                  if (modelHome != null && modelAway != null) ((modelHome + modelAway).toStringAsFixed(1), AppColors.cyan),
                ],
              ),
            ),
          ],
        ),
      ],
    );
  }
}

/// Stadium name and city/state, centred. Location only, never venue_indoor.
class _VenueLabel extends StatelessWidget {
  const _VenueLabel({required this.label});
  final String label;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        const Icon(Icons.location_on_outlined, size: 13, color: AppColors.inkMute),
        const SizedBox(width: 4),
        Flexible(child: Text(label, style: AppTextStyles.microLabel(color: AppColors.inkMute), textAlign: TextAlign.center)),
      ],
    );
  }
}

import 'package:flutter/material.dart';

import '../models/event.dart';
import '../models/live_score.dart';
import '../models/prediction.dart';
import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';
import '../../static/nfl_team_colors.dart';
import 'confidence_pill.dart';
import 'live_status_pill.dart';
import 'team_color_dot.dart';
import 'win_probability_bar.dart';

/// design/FRONTEND_STYLE.md's "Matchup hero (detail)" component: two
/// columns of team + score (win probability % small underneath), a split
/// bar, then the big PICK (with its own confidence pill right under it),
/// then a Pred total / home margin duo. liveState (from
/// liveScoresProvider) adds its own LIVE status/game-clock line between
/// the win-probability bar and PICK once set and live -- confidence and
/// the spread (PRED TOTAL/HOME MARGIN below) stay visible either way,
/// live or not, since both describe the pre-game pick, not the live game
/// state.
///
/// Pre-game, each column's only number is the model's own predicted
/// score, gradient-clipped cyan for the favored side. Once live, each
/// column shows its real in-progress score (gradient-clipped violet for
/// whichever side is currently ahead) with the same pre-game predicted
/// score still visible right below it in cyan -- live/actual and
/// predicted stay two distinctly colored numbers rather than one
/// swapping value, same convention game_row.dart's list rows use
/// (ink/cyan there; violet here since this big numeral doubles as the
/// "who's currently winning" signal, distinct from the pre-game favorite).
// Same breakpoint as game_row.dart's own _stackBreakpoint/
// field_leaderboard_table.dart's own _compactBreakpoint -- collapses the
// LIVE/confidence pills to just their colored dots on a phone-width card.
const _compactBreakpoint = 600.0;

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
    final homeFavored = prediction.homeWinProbability >= 0.5;
    final isLive = liveState?.live ?? false;
    final homeLiveScore = isLive ? liveState!.homeScore : null;
    final awayLiveScore = isLive ? liveState!.awayScore : null;
    // Who's ahead right now -- null pre-game (no live score yet) or on a
    // live tie, when neither side is "winning". Distinct from
    // homeFavored, which is the pre-game win-probability pick and stays
    // fixed for the whole game.
    final homeLeading = (homeLiveScore != null && awayLiveScore != null && homeLiveScore != awayLiveScore)
        ? homeLiveScore > awayLiveScore
        : null;

    return Container(
      padding: const EdgeInsets.all(28),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(20),
        gradient: LinearGradient(begin: Alignment.topLeft, end: Alignment.bottomRight, colors: AppColors.surfaceGrad),
        border: Border.all(color: AppColors.borderRaised),
      ),
      child: LayoutBuilder(
        builder: (context, constraints) {
          final compact = constraints.maxWidth < _compactBreakpoint;
          return Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(
                children: [
                  Expanded(
                    child: _TeamColumn(
                      color: away.primary,
                      abbr: away.abbreviation,
                      probability: 1 - prediction.homeWinProbability,
                      favored: !homeFavored,
                      predictedScore: prediction.awayScore,
                      liveScore: awayLiveScore,
                      leading: homeLeading == null ? null : !homeLeading,
                    ),
                  ),
                  Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 16),
                    // "@" reads as "away @ home"; away is the left column
                    // above so this ordering matches that. This is the
                    // only marker of which side is home/away.
                    child: Text('@', style: AppTextStyles.sectionTitle(color: AppColors.inkMute)),
                  ),
                  Expanded(
                    child: _TeamColumn(
                      color: home.primary,
                      abbr: home.abbreviation,
                      probability: prediction.homeWinProbability,
                      favored: homeFavored,
                      predictedScore: prediction.homeScore,
                      liveScore: homeLiveScore,
                      leading: homeLeading,
                    ),
                  ),
                ],
              ),
              if (event.venueLabel != null) ...[
                const SizedBox(height: 12),
                Center(child: _VenueLabel(label: event.venueLabel!)),
              ],
              const SizedBox(height: 20),
              WinProbabilityBar(homeWinProbability: prediction.homeWinProbability, height: 12),
              // Live status (game clock) is its own line, only present
              // once live -- separate from confidence, which lives with
              // the PICK below instead of next to the game clock (that
              // pairing read as describing the live game itself, not the
              // model's own pre-game pick).
              if (isLive) ...[
                const SizedBox(height: 12),
                Row(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    LiveStatusPill(dotOnly: compact),
                    if (liveState!.detail != null) ...[
                      const SizedBox(width: 8),
                      // Flexible + ellipsis -- ESPN's own detail text isn't
                      // always a short clock ("Q3 08:14"); situational
                      // strings ("End of 2nd Quarter", "Delayed: Weather")
                      // can run long enough to overflow a phone-width card
                      // otherwise (same guard game_row.dart's own live-detail
                      // text already uses).
                      Flexible(
                        child: Text(
                          liveState!.detail!,
                          style: AppTextStyles.body(color: AppColors.inkSub),
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                    ],
                  ],
                ),
              ],
              const SizedBox(height: 24),
              Center(
                child: Column(
                  children: [
                    Text('PICK', style: AppTextStyles.microLabel()),
                    const SizedBox(height: 4),
                    Text(homeFavored ? home.abbreviation : away.abbreviation, style: AppTextStyles.bigStatNumeral()),
                    const SizedBox(height: 8),
                    // Confidence stays visible live or not -- it's a
                    // property of the pre-game pick above it, not
                    // something the live game state changes.
                    ConfidencePill(homeWinProbability: prediction.homeWinProbability, dotOnly: compact),
                  ],
                ),
              ),
              const SizedBox(height: 24),
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceAround,
                children: [
                  Flexible(
                    child: _StatTrio(
                      label: 'PRED TOTAL',
                      value: (prediction.homeScore + prediction.awayScore).toStringAsFixed(1),
                    ),
                  ),
                  Flexible(child: _StatTrio(label: 'HOME MARGIN', value: prediction.margin.toStringAsFixed(1))),
                ],
              ),
            ],
          );
        },
      ),
    );
  }
}

class _TeamColumn extends StatelessWidget {
  const _TeamColumn({
    required this.color, required this.abbr, required this.probability, required this.favored,
    required this.predictedScore, this.liveScore, this.leading,
  });
  final Color? color;
  final String abbr;
  final double probability;
  // The pre-game win-probability pick -- fixed for the whole game, only
  // used to accent the predicted score before there's a live one.
  final bool favored;
  final double predictedScore;
  // Non-null only once the event is actually live -- this team's own
  // real, in-progress score.
  final double? liveScore;
  // Whether this team is currently ahead on the live scoreboard -- null
  // pre-game or on a live tie, when neither side is "winning" yet.
  final bool? leading;

  @override
  Widget build(BuildContext context) {
    // Predicted score always renders in cyan -- the sole number pre-game
    // (gradient-clipped when favored), or the smaller companion value
    // once a live score takes over the big numeral below.
    final predictedText = liveScore != null
        ? Text('${predictedScore.round()}', style: AppTextStyles.metricValue(color: AppColors.cyan))
        : Text('${predictedScore.round()} PTS', style: AppTextStyles.metricValueLarge(color: AppColors.cyan));
    final predictedNumeral = favored
        ? ShaderMask(
            shaderCallback: (bounds) => AppColors.cyanFill.createShader(bounds),
            blendMode: BlendMode.srcIn,
            child: predictedText,
          )
        : Opacity(opacity: 0.6, child: predictedText);

    Widget scoreDisplay;
    final live = liveScore;
    if (live != null) {
      // Live: this team's real, in-progress score is the big numeral,
      // gradient-clipped violet when currently ahead (dimmed otherwise)
      // -- distinct from the cyan predicted score, kept visible right
      // below it rather than swapped out.
      final liveNumeral = Text('${live.round()} PTS', style: AppTextStyles.metricValueLarge());
      scoreDisplay = Column(
        children: [
          leading == true
              ? ShaderMask(
                  shaderCallback: (bounds) => AppColors.violetFill.createShader(bounds),
                  blendMode: BlendMode.srcIn,
                  child: liveNumeral,
                )
              : Opacity(opacity: 0.6, child: liveNumeral),
          const SizedBox(height: 2),
          predictedNumeral,
        ],
      );
    } else {
      scoreDisplay = predictedNumeral;
    }

    return Column(
      children: [
        TeamColorDot(color: color, size: 10),
        const SizedBox(height: 8),
        Text(abbr, style: AppTextStyles.cardTitle()),
        const SizedBox(height: 8),
        scoreDisplay,
        const SizedBox(height: 4),
        Text('${(probability * 100).round()}%', style: AppTextStyles.microLabel(color: AppColors.inkMute)),
      ],
    );
  }
}

/// Completed-game counterpart to MatchupHero -- same card shape/team-color
/// language, but shows the final score plus the prediction actually
/// logged before the game was played, via `comparison` (already fetched
/// by the events list). `comparison` is null when no prediction was ever
/// logged for this event, not a loading state.
class MatchupResultHero extends StatelessWidget {
  const MatchupResultHero({super.key, required this.sport, required this.event, required this.comparison});

  final String sport;
  final SportEvent event;
  final PredictionComparison? comparison;

  @override
  Widget build(BuildContext context) {
    final home = teamDisplay(sport, event.home);
    final away = teamDisplay(sport, event.away);
    final homeWon = event.home.result?.won ?? false;

    return Container(
      padding: const EdgeInsets.all(28),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(20),
        gradient: LinearGradient(begin: Alignment.topLeft, end: Alignment.bottomRight, colors: AppColors.surfaceGrad),
        border: Border.all(color: AppColors.borderRaised),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              Expanded(
                child: _ResultTeamColumn(
                  color: away.primary, abbr: away.abbreviation,
                  score: event.away.result?.score, won: !homeWon,
                ),
              ),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16),
                // Away-left/home-right + "@", "FINAL" underneath adds the
                // completed-game status on top.
                child: Column(
                  children: [
                    Text('@', style: AppTextStyles.sectionTitle(color: AppColors.inkMute)),
                    Text('FINAL', style: AppTextStyles.microLabel(color: AppColors.inkMute)),
                  ],
                ),
              ),
              Expanded(
                child: _ResultTeamColumn(
                  color: home.primary, abbr: home.abbreviation,
                  score: event.home.result?.score, won: homeWon,
                ),
              ),
            ],
          ),
          if (event.venueLabel != null) ...[
            const SizedBox(height: 12),
            Center(child: _VenueLabel(label: event.venueLabel!)),
          ],
          const SizedBox(height: 24),
          _PredictionRecap(comparison: comparison),
        ],
      ),
    );
  }
}

class _ResultTeamColumn extends StatelessWidget {
  const _ResultTeamColumn({required this.color, required this.abbr, required this.score, required this.won});
  final Color? color;
  final String abbr;
  final double? score;
  final bool won;

  @override
  Widget build(BuildContext context) {
    final numeral = Text(score?.toStringAsFixed(0) ?? '--', style: AppTextStyles.bigStatNumeral());

    return Column(
      children: [
        TeamColorDot(color: color, size: 10),
        const SizedBox(height: 8),
        Text(abbr, style: AppTextStyles.cardTitle()),
        const SizedBox(height: 8),
        won
            ? ShaderMask(
                shaderCallback: (bounds) => AppColors.cyanFill.createShader(bounds),
                blendMode: BlendMode.srcIn,
                child: numeral,
              )
            : Opacity(opacity: 0.6, child: numeral),
      ],
    );
  }
}

class _PredictionRecap extends StatelessWidget {
  const _PredictionRecap({required this.comparison});
  final PredictionComparison? comparison;

  @override
  Widget build(BuildContext context) {
    final c = comparison;
    if (c == null) {
      return Center(
        child: Text('No prediction was recorded for this game.', style: AppTextStyles.body(color: AppColors.inkMute)),
      );
    }
    return Column(
      children: [
        Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(c.correct ? Icons.check_circle : Icons.cancel, color: c.correct ? AppColors.live : AppColors.neg, size: 18),
            const SizedBox(width: 8),
            Flexible(
              child: Text(
                c.correct ? 'Model picked the winner' : 'Model missed the winner',
                style: AppTextStyles.body(color: AppColors.inkSub),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
            ),
          ],
        ),
        const SizedBox(height: 16),
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceAround,
          children: [
            Flexible(child: _StatTrio(label: 'PREDICTED', value: '${(c.predictedHomeWinProbability * 100).round()}% HOME')),
            // '--' rather than omitting the trio when unavailable -- keeps
            // the 3-slot spaceAround layout stable instead of reflowing.
            Flexible(child: _StatTrio(label: 'PRED MARGIN', value: c.predictedMargin?.toStringAsFixed(1) ?? '--')),
            Flexible(child: _StatTrio(label: 'ACTUAL MARGIN', value: c.actualMargin.toStringAsFixed(1))),
          ],
        ),
      ],
    );
  }
}

class _StatTrio extends StatelessWidget {
  const _StatTrio({required this.label, required this.value});
  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Text(label, style: AppTextStyles.microLabel(), maxLines: 1, overflow: TextOverflow.ellipsis),
        const SizedBox(height: 4),
        Text(value, style: AppTextStyles.metricValueLarge()),
      ],
    );
  }
}

/// Stadium name + city/state, between the score/probability row above and
/// the PICK/margin section below -- see SportEvent.venueLabel. Caller
/// skips rendering entirely when it's null; deliberately excludes
/// venue_indoor -- location/name only.
class _VenueLabel extends StatelessWidget {
  const _VenueLabel({required this.label});
  final String label;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(Icons.location_on_outlined, size: 13, color: AppColors.inkMute),
        const SizedBox(width: 4),
        Flexible(
          child: Text(
            label,
            style: AppTextStyles.microLabel(color: AppColors.inkMute),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
        ),
      ],
    );
  }
}

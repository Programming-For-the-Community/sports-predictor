import '../../core/models/sport_config.dart';

// Synthesized final-round labels for the bracket-shaped widgets on the
// season page -- these aren't backend data (unlike BracketRound.round for
// every other round, which is whatever the backend named it), so they're
// authored here rather than parsed.
abstract final class BracketRoundLabels {
  static const superBowl = 'Super Bowl';
  static const championship = 'Championship';
  static const stanleyCupFinal = 'Stanley Cup Final';

  /// The league final's own name for `sport`.
  static String finalFor(String sport) => switch (sport) {
        SportIds.nfl => superBowl,
        SportIds.nhl => stanleyCupFinal,
        _ => championship,
      };
  static const firstFour = 'First Four';
}

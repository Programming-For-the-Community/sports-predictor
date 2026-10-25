/// UPSET and UPSET WATCH for a head-to-head game. The sportsbook's pre-game
/// favourite is who's favoured; the model only says how close it is. Only a
/// favourite at [upsetMinFavoriteMoneyline] or shorter counts.
enum UpsetFlag {
  /// The betting favourite lost.
  upset('UPSET'),

  /// Not yet final, and the model gives the betting favourite at most
  /// [upsetWatchMaxFavoritePercent].
  watch('UPSET WATCH');

  const UpsetFlag(this.label);

  final String label;
}

/// The model's highest chance for the betting favourite, as the row shows it
/// (a whole percent), that still counts as an upset watch.
const upsetWatchMaxFavoritePercent = 60;

/// The longest moneyline a betting favourite can have and still be upset.
const upsetMinFavoriteMoneyline = -200;

/// [favorite] is 'home'/'away' or null, [favoriteMoneyline] its American
/// odds. A final game needs [homeWon] (null for a tie); one not yet final
/// needs the model's [homeWinProbability].
UpsetFlag? upsetFlag({
  required String? favorite,
  required int? favoriteMoneyline,
  required bool isFinal,
  bool? homeWon,
  double? homeWinProbability,
}) {
  if (favorite != 'home' && favorite != 'away') return null;
  if (favoriteMoneyline == null || favoriteMoneyline > upsetMinFavoriteMoneyline) return null;
  final favoriteIsHome = favorite == 'home';
  if (isFinal) {
    if (homeWon == null) return null;
    return homeWon == favoriteIsHome ? null : UpsetFlag.upset;
  }
  if (homeWinProbability == null) return null;
  final favoriteChance = favoriteIsHome ? homeWinProbability : 1 - homeWinProbability;
  return (favoriteChance * 100).round() <= upsetWatchMaxFavoritePercent ? UpsetFlag.watch : null;
}

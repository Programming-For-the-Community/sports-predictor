// Which player-prop stats the home-screen widgets list, and the rules for
// picking the day's rows (core/mobile/widget_data.dart's buildTopProps).

import '../core/models/sport_config.dart';

class PropStat {
  const PropStat({required this.bigGame, required this.unit, required this.decimals});

  /// What counts as a big game for this stat, in the stat's own units.
  final double bigGame;

  /// Short unit after the projection: "pass yds", "reb".
  final String unit;

  /// Decimal places for the projection and its tolerance.
  final int decimals;
}

// Keyed by the stat key the prediction's `leaders` block uses.
const _footballPropStats = <String, PropStat>{
  'passing_yards': PropStat(bigGame: 300, unit: 'pass yds', decimals: 0),
  'rushing_yards': PropStat(bigGame: 100, unit: 'rush yds', decimals: 0),
  'receiving_yards': PropStat(bigGame: 100, unit: 'rec yds', decimals: 0),
  'passing_touchdowns': PropStat(bigGame: 3, unit: 'pass TDs', decimals: 1),
};

const _basketballPropStats = <String, PropStat>{
  'points': PropStat(bigGame: 30, unit: 'pts', decimals: 1),
  'rebounds': PropStat(bigGame: 12, unit: 'reb', decimals: 1),
  'assists': PropStat(bigGame: 10, unit: 'ast', decimals: 1),
};

const _hockeyPropStats = <String, PropStat>{
  'goals': PropStat(bigGame: 1, unit: 'goals', decimals: 1),
  'shots_total': PropStat(bigGame: 5, unit: 'SOG', decimals: 1),
  'saves': PropStat(bigGame: 45, unit: 'saves', decimals: 0),
};

/// The sports with player-props widgets, each with the stats it lists.
const propStatsBySport = <String, Map<String, PropStat>>{
  SportIds.nfl: _footballPropStats,
  SportIds.ncaafb: _footballPropStats,
  SportIds.nba: _basketballPropStats,
  SportIds.ncaambb: _basketballPropStats,
  SportIds.nhl: _hockeyPropStats,
};

/// A projection is listed only once it reaches this share of its stat's big game.
const propFloorShare = 0.6;

/// At most this many rows for any one stat.
const maxPropsPerStat = 2;

/// Rows kept for the widgets; each shows as many as its size fits.
const maxProps = 10;

/// "passing_yards" -> "player-prop-passing-yards", the model that projects it.
String propModelName(String statKey) => 'player-prop-${statKey.replaceAll('_', '-')}';

// Plain-English naming and units for each backend model, in one place --
// shared by the Models tab (training cards) and the Performance tab
// (season/last-period cards) so a model reads the same on both.

/// "win-probability" -> "Win Probability".
String modelDisplayName(String modelName) => modelName.split('-').map((w) => w.isEmpty ? w : '${w[0].toUpperCase()}${w.substring(1)}').join(' ');

class ModelDisplay {
  const ModelDisplay({required this.countNoun, required this.valueUnit, required this.missDecimals, required this.rangeDecimals});

  /// What one graded prediction is called: "games", "teams" or "players".
  final String countNoun;

  /// Short unit after a value: "pts", "yds", "TDs" -- empty when none applies.
  final String valueUnit;

  /// Decimal places for an average miss / margin of error.
  final int missDecimals;

  /// Decimal places for the low/high ends of a predicted-amount band.
  final int rangeDecimals;
}

const _gameModel = ModelDisplay(countNoun: 'games', valueUnit: '', missDecimals: 1, rangeDecimals: 1);
const _teamScoreModel = ModelDisplay(countNoun: 'teams', valueUnit: 'pts', missDecimals: 1, rangeDecimals: 0);
const _marginModel = ModelDisplay(countNoun: 'games', valueUnit: 'pts', missDecimals: 1, rangeDecimals: 1);

// Player-prop stat -> (unit, decimals). A count that's usually 0-3 (TDs,
// sacks) needs a decimal in a band's range; a total in the tens or hundreds
// doesn't.
const _propUnits = <String, (String, int, int)>{
  'yards': ('yds', 1, 0),
  'touchdowns': ('TDs', 2, 1),
  'sacks': ('sacks', 1, 1),
  'points': ('pts', 1, 0),
  'rebounds': ('reb', 1, 0),
  'assists': ('ast', 1, 0),
  'steals': ('stl', 1, 1),
  'blocks': ('blk', 1, 1),
  'three-pointers-made': ('3PM', 1, 1),
};

// PGA / F1 amount models. A golfer's projected score and each round are in
// strokes; a finishing or grid slot is in places.
const _strokesModel = ModelDisplay(countNoun: 'golfers', valueUnit: 'strokes', missDecimals: 1, rangeDecimals: 1);
const _placesModel = ModelDisplay(countNoun: 'drivers', valueUnit: 'places', missDecimals: 1, rangeDecimals: 1);

ModelDisplay modelDisplay(String modelName) {
  if (modelName == 'projected-score-to-par' || RegExp(r'^round-[1-4]$').hasMatch(modelName)) return _strokesModel;
  if (modelName.startsWith('projected-') && modelName.endsWith('-position')) return _placesModel;
  if (modelName == 'projected-sprint-grid-position') return _placesModel;
  if (modelName == 'score-margin') return _marginModel;
  if (modelName == 'home-score' || modelName == 'away-score') return _teamScoreModel;
  if (modelName.startsWith('player-prop-')) {
    final stat = modelName.substring('player-prop-'.length);
    for (final entry in _propUnits.entries) {
      if (stat.endsWith(entry.key)) {
        return ModelDisplay(countNoun: 'players', valueUnit: entry.value.$1, missDecimals: entry.value.$2, rangeDecimals: entry.value.$3);
      }
    }
    return const ModelDisplay(countNoun: 'players', valueUnit: '', missDecimals: 1, rangeDecimals: 1);
  }
  return _gameModel;
}

// What a yes/no chance model is the chance OF, for the sentence "The top-10
// chance we gave each golfer."
const _chanceNouns = {
  'top-10-probability': 'top-10',
  'top-5-probability': 'top-5',
  'win-probability': 'win',
  'sprint-win-probability': 'win',
  'podium-probability': 'podium',
  'constructor-win-probability': 'win',
  'sprint-podium-probability': 'podium',
  'dnf-probability': 'DNF',
};

String chanceNoun(String modelName) => _chanceNouns[modelName] ?? 'this';

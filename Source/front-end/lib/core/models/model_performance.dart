/// Mirrors GET /{sport}/model-performance's response shape (see
/// library/performance/scorecard.py): how each promoted model has actually
/// performed this season and in the most recent period (a week for football
/// and basketball, one event for PGA/F1).
///
/// Two kinds of model, told apart by `kind`:
///  * "pick"   -- a yes/no call (who wins). `value`s are accuracy (0..1).
///               Bands are confidence tiers; each band's `pct` is the share
///               of its picks that were right.
///  * "chance" -- a yes/no probability (top 10, podium). Accuracy again; bands
///               are fifths of the chance we gave, `pct` the share called right.
///  * "amount" -- a number (margin, score, a player's yards). `value`s are
///               the average miss in the stat's own units. Bands are equal
///               thirds of the predicted values (`lo`..`hi`); each band's
///               `pct` is the share that landed within `marginOfError`.
class PerformanceWindow {
  const PerformanceWindow({required this.value, required this.n, this.label});

  /// Null when nothing has been graded in the window.
  final double? value;
  final int n;

  /// Short period name ("Wk 3") -- set on the last period, not the season.
  final String? label;

  factory PerformanceWindow.fromJson(Map<String, dynamic> json) => PerformanceWindow(
        value: (json['value'] as num?)?.toDouble(),
        n: json['n'] as int,
        label: json['label'] as String?,
      );
}

class PerformanceBand {
  const PerformanceBand({
    required this.tag,
    required this.lo,
    required this.hi,
    required this.n,
    required this.pct,
    required this.early,
    this.bias,
  });

  final String tag;
  final double? lo;
  final double? hi;
  final int n;

  /// 0..1. Null when `early` -- too few predictions for a percentage to mean anything.
  final double? pct;
  final bool early;

  /// Amount bands only: the average of prediction minus actual for this band --
  /// positive means the model tended to miss high, negative low. Null when early.
  final double? bias;

  factory PerformanceBand.fromJson(Map<String, dynamic> json) => PerformanceBand(
        tag: json['tag'] as String,
        lo: (json['lo'] as num?)?.toDouble(),
        hi: (json['hi'] as num?)?.toDouble(),
        n: json['n'] as int,
        pct: (json['pct'] as num?)?.toDouble(),
        early: json['early'] as bool? ?? false,
        bias: (json['bias'] as num?)?.toDouble(),
      );
}

/// One team or player in a model's "most accurate on" list.
class BestEntity {
  const BestEntity({required this.entityId, required this.value, required this.n, this.name, this.abbreviation, this.color});

  final String entityId;

  /// Accuracy (0..1) for a pick or chance model; average miss for an amount,
  /// or miss as a share of actual (0..1) in a relative ranking.
  final double value;

  /// How many of its predictions were graded.
  final int n;
  final String? name;
  final String? abbreviation;
  final String? color;

  factory BestEntity.fromJson(Map<String, dynamic> json) => BestEntity(
        entityId: json['entity_id'] as String,
        value: (json['value'] as num).toDouble(),
        n: json['n'] as int,
        name: json['name'] as String?,
        abbreviation: json['abbreviation'] as String?,
        color: json['color'] as String?,
      );
}

/// Best-first; `entityType` is "team" or "player".
class BestRanking {
  const BestRanking({required this.entityType, required this.entities});

  final String entityType;
  final List<BestEntity> entities;

  factory BestRanking.fromJson(Map<String, dynamic> json) => BestRanking(
        entityType: json['entity_type'] as String,
        entities: (json['entities'] as List<dynamic>? ?? [])
            .map((e) => BestEntity.fromJson(e as Map<String, dynamic>))
            .toList(),
      );
}

class ModelPerformanceRecord {
  const ModelPerformanceRecord({
    required this.modelName,
    required this.version,
    required this.kind,
    required this.bandKind,
    required this.season,
    required this.lastPeriod,
    required this.periods,
    required this.vsBaselinePct,
    required this.atTraining,
    required this.marginOfError,
    required this.bands,
    this.bias,
    this.countNoun,
    this.best,
    this.bestRelative,
  });

  static const kindPick = 'pick';
  static const kindChance = 'chance';
  static const kindAmount = 'amount';
  static const bandKindConfidence = 'confidence';
  static const bandKindChance = 'predicted_chance';

  final String modelName;
  final int? version;
  final String kind;
  final String bandKind;
  final PerformanceWindow season;
  final PerformanceWindow? lastPeriod;
  final List<PerformanceWindow> periods;

  /// Relative lift over the naive baseline, in percent (positive = better).
  final double? vsBaselinePct;

  /// The model's own number when it was trained: accuracy (0..1) or average miss.
  final double? atTraining;

  /// Amount models only: how far off a prediction can be and still count as a hit.
  final double? marginOfError;
  final List<PerformanceBand> bands;

  /// Amount models only: the season's average of prediction minus actual.
  final double? bias;

  /// What one graded prediction is called when the sport says so ("golfers",
  /// "drivers"); null falls back to the app's own per-model default.
  final String? countNoun;

  /// The teams or players this model has been most accurate on. Null from an
  /// older scorecard.
  final BestRanking? best;

  /// Player props only: the same, ranked by miss as a share of actual.
  final BestRanking? bestRelative;

  bool get isPick => kind == kindPick;

  /// A number model (average miss); every other kind is graded right/wrong (accuracy).
  bool get isAmount => kind == kindAmount;
  bool get hasResults => season.n > 0;

  factory ModelPerformanceRecord.fromJson(Map<String, dynamic> json) => ModelPerformanceRecord(
        modelName: json['model_name'] as String,
        version: json['version'] as int?,
        kind: json['kind'] as String,
        bandKind: json['band_kind'] as String,
        season: PerformanceWindow.fromJson(json['season'] as Map<String, dynamic>),
        lastPeriod: json['last_period'] == null ? null : PerformanceWindow.fromJson(json['last_period'] as Map<String, dynamic>),
        periods: (json['periods'] as List<dynamic>? ?? [])
            .map((p) => PerformanceWindow.fromJson(p as Map<String, dynamic>))
            .toList(),
        vsBaselinePct: (json['vs_baseline_pct'] as num?)?.toDouble(),
        atTraining: (json['at_training'] as num?)?.toDouble(),
        marginOfError: (json['margin_of_error'] as num?)?.toDouble(),
        bias: (json['bias'] as num?)?.toDouble(),
        countNoun: json['count_noun'] as String?,
        best: json['best'] == null ? null : BestRanking.fromJson(json['best'] as Map<String, dynamic>),
        bestRelative: json['best_relative'] == null ? null : BestRanking.fromJson(json['best_relative'] as Map<String, dynamic>),
        bands: (json['bands'] as List<dynamic>? ?? [])
            .map((b) => PerformanceBand.fromJson(b as Map<String, dynamic>))
            .toList(),
      );
}

class ModelPerformance {
  const ModelPerformance({
    required this.sport,
    required this.season,
    required this.periodKind,
    required this.models,
    this.windowDays,
  });

  static const periodKindWeek = 'week';

  final String sport;
  final int? season;

  /// "week" or "event" -- what "last period" means for this sport.
  final String periodKind;
  final List<ModelPerformanceRecord> models;

  /// Set when the scorecard covers only the last N days rather than the whole
  /// season (a sport with too many games to grade all season, e.g. NCAAMBB).
  final int? windowDays;

  bool get isWeekly => periodKind == periodKindWeek;

  factory ModelPerformance.fromJson(Map<String, dynamic> json) => ModelPerformance(
        sport: json['sport'] as String,
        season: json['season'] as int?,
        periodKind: json['period_kind'] as String? ?? periodKindWeek,
        windowDays: json['window_days'] as int?,
        models: (json['models'] as List<dynamic>? ?? [])
            .map((m) => ModelPerformanceRecord.fromJson(m as Map<String, dynamic>))
            .toList(),
      );
}

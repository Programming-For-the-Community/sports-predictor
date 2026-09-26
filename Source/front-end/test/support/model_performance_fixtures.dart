import 'package:front_end/core/models/model_performance.dart';

PerformanceBand band(String tag, {double? lo, double? hi, int n = 10, double? pct = 0.5, bool early = false, double? bias}) =>
    PerformanceBand(tag: tag, lo: lo, hi: hi, n: n, pct: early ? null : pct, early: early, bias: early ? null : bias);

/// Win-probability card: a pick model with the app's confidence tiers.
ModelPerformanceRecord pickRecord({
  String modelName = 'win-probability',
  double season = 0.684,
  int seasonN = 32,
  double? last = 0.75,
  int lastN = 12,
  List<PerformanceBand>? bands,
  double? vsBaselinePct = 14,
  double? atTraining = 0.661,
}) =>
    ModelPerformanceRecord(
      modelName: modelName,
      version: 9,
      kind: ModelPerformanceRecord.kindPick,
      bandKind: ModelPerformanceRecord.bandKindConfidence,
      season: PerformanceWindow(value: season, n: seasonN),
      lastPeriod: last == null ? null : PerformanceWindow(value: last, n: lastN, label: 'Wk 3'),
      periods: const [
        PerformanceWindow(value: 0.62, n: 16, label: 'Wk 1'),
        PerformanceWindow(value: 0.69, n: 16, label: 'Wk 2'),
        PerformanceWindow(value: 0.75, n: 12, label: 'Wk 3'),
      ],
      vsBaselinePct: vsBaselinePct,
      atTraining: atTraining,
      marginOfError: null,
      bands: bands ??
          [
            band('HIGH', lo: 0.13, n: 9, pct: 0.89),
            band('MED', lo: 0.06, hi: 0.13, n: 13, pct: 0.69),
            band('LOW', lo: 0, hi: 0.06, n: 10, pct: 0.5),
          ],
    );

/// An amount model (margin, score, a player stat): equal-thirds bands.
ModelPerformanceRecord amountRecord({
  String modelName = 'score-margin',
  double season = 10.2,
  int seasonN = 32,
  double? last = 8.9,
  int lastN = 16,
  double marginOfError = 10.8,
  List<PerformanceBand>? bands,
  double? vsBaselinePct = 11,
  double? atTraining = 10.8,
  double? bias = 2.4,
}) =>
    ModelPerformanceRecord(
      modelName: modelName,
      version: 6,
      kind: ModelPerformanceRecord.kindAmount,
      bandKind: 'predicted_amount',
      season: PerformanceWindow(value: season, n: seasonN),
      lastPeriod: last == null ? null : PerformanceWindow(value: last, n: lastN, label: 'Wk 3'),
      periods: [
        const PerformanceWindow(value: 11.9, n: 16, label: 'Wk 1'),
        const PerformanceWindow(value: 10.4, n: 16, label: 'Wk 2'),
        if (last != null) PerformanceWindow(value: last, n: lastN, label: 'Wk 3'),
      ],
      vsBaselinePct: vsBaselinePct,
      atTraining: atTraining,
      marginOfError: marginOfError,
      bias: bias,
      bands: bands ??
          [
            band('LOW', lo: 0.5, hi: 6.0, n: 11, pct: 0.64, bias: 3.1),
            band('MED', lo: 6.0, hi: 11.5, n: 11, pct: 0.55, bias: -0.4),
            band('HIGH', lo: 11.5, hi: 17.0, n: 10, pct: 0.5, bias: -4.6),
          ],
    );

/// A yes/no chance model (PGA top 10, F1 podium): accuracy, with bands by the chance we gave.
ModelPerformanceRecord chanceRecord({
  String modelName = 'top-10-probability',
  String countNoun = 'golfers',
  double season = 0.812,
  double? last = 0.839,
  List<PerformanceBand>? bands,
}) =>
    ModelPerformanceRecord(
      modelName: modelName,
      version: 5,
      kind: ModelPerformanceRecord.kindChance,
      bandKind: ModelPerformanceRecord.bandKindChance,
      season: PerformanceWindow(value: season, n: 812),
      lastPeriod: last == null ? null : PerformanceWindow(value: last, n: 144, label: 'Biltmore Championship'),
      periods: const [
        PerformanceWindow(value: 0.79, n: 140, label: 'Event 3'),
        PerformanceWindow(value: 0.82, n: 144, label: 'Event 4'),
        PerformanceWindow(value: 0.839, n: 144, label: 'Biltmore Championship'),
      ],
      vsBaselinePct: -3,
      atTraining: 0.804,
      marginOfError: null,
      countNoun: countNoun,
      bands: bands ??
          [
            band('LOW', lo: 0, hi: 0.2, n: 402, pct: 0.94),
            band('LOW', lo: 0.2, hi: 0.4, n: 214, pct: 0.73),
            band('MED', lo: 0.4, hi: 0.6, n: 118, pct: 0.52),
            band('HIGH', lo: 0.6, hi: 0.8, n: 78, pct: 0.74),
            band('HIGH', lo: 0.8, hi: 1.0, n: 3, early: true),
          ],
    );

/// A promoted model nothing has been graded against yet.
ModelPerformanceRecord emptyRecord({String modelName = 'player-prop-rushing-touchdowns'}) => ModelPerformanceRecord(
      modelName: modelName,
      version: 2,
      kind: ModelPerformanceRecord.kindAmount,
      bandKind: 'predicted_amount',
      season: const PerformanceWindow(value: null, n: 0),
      lastPeriod: null,
      periods: const [],
      vsBaselinePct: null,
      atTraining: null,
      marginOfError: null,
      bands: const [],
    );

/// The full NFL set, in worst-case-ish shapes: every card kind, an early band, an empty card.
List<ModelPerformanceRecord> fullNflSet() => [
      pickRecord(),
      amountRecord(),
      amountRecord(
        modelName: 'home-score',
        season: 7.9,
        last: 6.8,
        marginOfError: 7.4,
        atTraining: 7.6,
        bands: [
          band('LOW', lo: 14, hi: 20, n: 10, pct: 0.6),
          band('MED', lo: 20, hi: 27, n: 14, pct: 0.57),
          band('HIGH', lo: 27, hi: 33, n: 8, pct: 0.5),
        ],
      ),
      amountRecord(
        modelName: 'player-prop-passing-yards',
        season: 58.2,
        last: 61.4,
        marginOfError: 57.5,
        atTraining: 57.5,
        bands: [
          band('LOW', lo: 120, hi: 193, n: 10, pct: 0.7, bias: 12.34),
          band('MED', lo: 193, hi: 267, n: 11, pct: 0.55, bias: -8.0),
          band('HIGH', lo: 267, hi: 340, n: 10, pct: 0.5, bias: -31.25),
        ],
      ),
      amountRecord(
        modelName: 'player-prop-sacks',
        season: 0.9,
        last: 0.9,
        marginOfError: 0.9,
        atTraining: 0.9,
        bands: [
          band('LOW', lo: 0.2, hi: 0.6, n: 8, pct: 0.75, bias: 0.31),
          band('MED', lo: 0.6, hi: 1.0, n: 6, pct: 0.67, bias: -0.02),
          band('HIGH', lo: 1.0, hi: 1.4, n: 4, early: true),
        ],
      ),
      emptyRecord(),
    ];

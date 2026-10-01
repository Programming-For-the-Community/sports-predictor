import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/data/model_performance_repository.dart';
import '../../core/models/model_performance.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/model_card_grid.dart';
import '../../core/widgets/model_performance_accordion.dart';
import '../../core/widgets/model_performance_card_view.dart';

// The order a reader expects: the headline pick first, then the game-level
// numbers, then everything else (player props) alphabetically.
const _leadingModels = ['win-probability', 'score-margin', 'home-score', 'away-score'];

// Below this width (the same compact breakpoint GameRow and MatchupHero use)
// each model is a one-line row that opens into its card, instead of a column
// of full cards.
const _accordionBreakpoint = 600.0;

List<ModelPerformanceRecord> orderedPerformanceModels(List<ModelPerformanceRecord> models) {
  int rank(ModelPerformanceRecord m) {
    final index = _leadingModels.indexOf(m.modelName);
    return index == -1 ? _leadingModels.length : index;
  }

  return [...models]..sort((a, b) {
      final byRank = rank(a).compareTo(rank(b));
      return byRank != 0 ? byRank : a.modelName.compareTo(b.modelName);
    });
}

/// The per-sport Performance tab: one card per promoted model showing how it
/// has done this season and in the most recent period. Same card shell and
/// grid as the Models tab; on a phone, a list of one-line rows that each open
/// into the card.
class ModelPerformancePage extends ConsumerWidget {
  const ModelPerformancePage({super.key, required this.sportId});

  final String sportId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final performance = ref.watch(modelPerformanceProvider(sportId));

    return RefreshIndicator(
      onRefresh: () => ref.refresh(modelPerformanceProvider(sportId).future),
      child: SingleChildScrollView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.all(24),
        child: performance.when(
          data: (data) {
            if (data.models.isEmpty) {
              return Text(
                'No results yet -- check back once games have been played.',
                style: AppTextStyles.body(color: AppColors.inkSub),
              );
            }
            return Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                _Heading(performance: data),
                const SizedBox(height: 20),
                _Models(performance: data, sportId: sportId),
              ],
            );
          },
          loading: () => const Center(child: Padding(padding: EdgeInsets.all(40), child: CircularProgressIndicator())),
          error: (error, _) => Text('Couldn\'t load performance: $error', style: AppTextStyles.body(color: AppColors.neg)),
        ),
      ),
    );
  }
}

class _Models extends StatelessWidget {
  const _Models({required this.performance, required this.sportId});

  final ModelPerformance performance;
  final String sportId;

  @override
  Widget build(BuildContext context) {
    final records = orderedPerformanceModels(performance.models);
    final windowDays = performance.windowDays;
    final seasonLabel = windowDays == null ? 'THIS SEASON' : 'LAST $windowDays DAYS';
    return LayoutBuilder(
      builder: (context, constraints) {
        if (constraints.maxWidth < _accordionBreakpoint) {
          return ModelPerformanceAccordion(records: records, isWeekly: performance.isWeekly, seasonLabel: seasonLabel, sport: sportId);
        }
        return ModelCardGrid<ModelPerformanceRecord>(
          // Three across on a 1920px screen, two on a 1366px laptop.
          minCardWidth: 600,
          items: records,
          cardBuilder: (record) => ModelPerformanceCardView(
            record: record,
            sport: sportId,
            isWeekly: performance.isWeekly,
            seasonLabel: seasonLabel,
          ),
        );
      },
    );
  }
}

class _Heading extends StatelessWidget {
  const _Heading({required this.performance});

  final ModelPerformance performance;

  @override
  Widget build(BuildContext context) {
    final through = performance.models.map((m) => m.lastPeriod?.label).whereType<String>().firstOrNull;
    final season = performance.season;
    final windowDays = performance.windowDays;
    final parts = [
      if (windowDays != null) 'Last $windowDays days' else if (season != null) '$season season',
      if (through != null) 'through $through',
    ];
    if (parts.isEmpty) return const SizedBox.shrink();
    return Text(parts.join(' · '), style: AppTextStyles.sectionTitle());
  }
}

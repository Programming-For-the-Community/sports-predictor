import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/data/model_performance_repository.dart';
import '../../core/models/model_performance.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/model_card_grid.dart';
import '../../core/widgets/model_performance_card_view.dart';

// The order a reader expects: the headline pick first, then the game-level
// numbers, then everything else (player props) alphabetically.
const _leadingModels = ['win-probability', 'score-margin', 'home-score', 'away-score'];

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
/// grid as the Models tab -- a single column on a phone.
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
                ModelCardGrid<ModelPerformanceRecord>(
                  equalHeight: false,
                  // Wide enough for a card's "most accurate on" list to sit beside its results.
                  idealCardWidth: 880,
                  items: orderedPerformanceModels(data.models),
                  cardBuilder: (record) => ModelPerformanceCardView(
                    record: record,
                    sport: sportId,
                    isWeekly: data.isWeekly,
                    seasonLabel: data.windowDays == null ? 'THIS SEASON' : 'LAST ${data.windowDays} DAYS',
                  ),
                ),
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

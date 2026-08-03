import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/data/models_repository.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/model_card_view.dart';

class ModelCardsPage extends ConsumerWidget {
  const ModelCardsPage({super.key, required this.sportId});

  final String sportId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final models = ref.watch(modelsListProvider(sportId));

    return SingleChildScrollView(
      padding: const EdgeInsets.all(24),
      child: models.when(
        data: (list) {
          if (list.isEmpty) {
            return Text('No models have been promoted yet.', style: AppTextStyles.body(color: AppColors.inkSub));
          }
          return Wrap(
            spacing: 20,
            runSpacing: 20,
            children: [
              for (final model in list) SizedBox(width: 420, child: ModelCardView(model: model)),
            ],
          );
        },
        loading: () => const Center(child: Padding(padding: EdgeInsets.all(40), child: CircularProgressIndicator())),
        error: (error, _) => Text('Couldn\'t load models: $error', style: AppTextStyles.body(color: AppColors.neg)),
      ),
    );
  }
}

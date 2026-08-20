import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/data/models_repository.dart';
import '../../core/models/model_card.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/model_card_view.dart';
import '../../core/widgets/responsive.dart';

const _cardWidth = 420.0;
const _cardSpacing = 20.0;

class ModelCardsPage extends ConsumerWidget {
  const ModelCardsPage({super.key, required this.sportId});

  final String sportId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final models = ref.watch(modelsListProvider(sportId));

    return RefreshIndicator(
      onRefresh: () => ref.refresh(modelsListProvider(sportId).future),
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(24),
        child: models.when(
          data: (list) {
            if (list.isEmpty) {
              return Text('No models have been promoted yet.', style: AppTextStyles.body(color: AppColors.inkSub));
            }
            return _ModelCardGrid(models: list);
          },
          loading: () => const Center(child: Padding(padding: EdgeInsets.all(40), child: CircularProgressIndicator())),
          error: (error, _) => Text('Couldn\'t load models: $error', style: AppTextStyles.body(color: AppColors.neg)),
        ),
      ),
    );
  }
}

/// Cards are chunked into rows by available width (via LayoutBuilder),
/// each row stretched via IntrinsicHeight so every card in it matches the
/// tallest one. Card height varies with content (the COMPARED AGAINST
/// section only appears on cards with more than one recorded candidate,
/// titles wrap to 1 or 2 lines depending on the model name).
class _ModelCardGrid extends StatelessWidget {
  const _ModelCardGrid({required this.models});

  final List<ModelCard> models;

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final perRow = ((constraints.maxWidth + _cardSpacing) / (_cardWidth + _cardSpacing)).floor().clamp(1, 999);
        // perRow floors at 1 even when the viewport itself is narrower
        // than one card (a phone screen) -- capped here so that lone card
        // shrinks to fit instead of overflowing the Row that lays it out.
        final width = cardWidth(_cardWidth, constraints.maxWidth);

        final rows = <List<ModelCard>>[];
        for (var i = 0; i < models.length; i += perRow) {
          rows.add(models.sublist(i, (i + perRow).clamp(0, models.length)));
        }

        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            for (final row in rows) ...[
              IntrinsicHeight(
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    for (final model in row) ...[
                      SizedBox(width: width, child: ModelCardView(model: model)),
                      if (model != row.last) const SizedBox(width: _cardSpacing),
                    ],
                  ],
                ),
              ),
              if (row != rows.last) const SizedBox(height: _cardSpacing),
            ],
          ],
        );
      },
    );
  }
}

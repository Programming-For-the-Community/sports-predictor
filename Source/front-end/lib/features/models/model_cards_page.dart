import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/data/models_repository.dart';
import '../../core/models/model_card.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/model_card_view.dart';

const _cardWidth = 420.0;
const _cardSpacing = 20.0;

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
          return _ModelCardGrid(models: list);
        },
        loading: () => const Center(child: Padding(padding: EdgeInsets.all(40), child: CircularProgressIndicator())),
        error: (error, _) => Text('Couldn\'t load models: $error', style: AppTextStyles.body(color: AppColors.neg)),
      ),
    );
  }
}

/// Rows of fixed-width cards, each row stretched (via IntrinsicHeight) so
/// every card in that row matches the tallest one -- card height varies
/// for real content reasons (the COMPARED AGAINST section only appears
/// on cards with more than one recorded candidate, titles wrap to 1 or 2
/// lines depending on the model name), so this evens them out visually
/// without a fixed height that would risk clipping a taller card's
/// content. A plain Wrap can't do this itself -- it has no concept of
/// "everything in this row", which is exactly what IntrinsicHeight needs
/// to stretch against. Rows are chunked here (via LayoutBuilder, so the
/// column count adapts to how many 420-wide cards actually fit) rather
/// than left to Wrap's own automatic flow.
class _ModelCardGrid extends StatelessWidget {
  const _ModelCardGrid({required this.models});

  final List<ModelCard> models;

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final perRow = ((constraints.maxWidth + _cardSpacing) / (_cardWidth + _cardSpacing)).floor().clamp(1, 999);

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
                      SizedBox(width: _cardWidth, child: ModelCardView(model: model)),
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

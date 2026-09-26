import 'package:flutter/material.dart';

import 'responsive.dart';

const _cardWidth = 420.0;
const _cardSpacing = 20.0;

/// Lays model cards out as equal-height rows of as many fixed-width columns as
/// fit -- always exactly one column on a phone (a card is never narrower than
/// the screen allows, and never wider than its ideal width). Shared by the
/// Models and Performance tabs.
///
/// `equalHeight` stretches every card in a row to the tallest (the Models tab's
/// look). It measures each card's intrinsic height, so it can't be used with a
/// card that contains a LayoutBuilder -- the Performance cards do, and their
/// heights differ a lot, so they leave it off and let content set the height.
class ModelCardGrid<T> extends StatelessWidget {
  const ModelCardGrid({super.key, required this.items, required this.cardBuilder, this.equalHeight = true});

  final List<T> items;
  final Widget Function(T item) cardBuilder;
  final bool equalHeight;

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final perRow = ((constraints.maxWidth + _cardSpacing) / (_cardWidth + _cardSpacing)).floor().clamp(1, 999);
        final width = cardWidth(_cardWidth, constraints.maxWidth);

        final rows = <List<T>>[];
        for (var i = 0; i < items.length; i += perRow) {
          rows.add(items.sublist(i, (i + perRow).clamp(0, items.length)));
        }

        // A single column has nothing to equalize against.
        final stretch = equalHeight && perRow > 1;

        Widget rowOf(List<T> row) {
          final cards = Row(
            crossAxisAlignment: stretch ? CrossAxisAlignment.stretch : CrossAxisAlignment.start,
            children: [
              for (var c = 0; c < row.length; c++) ...[
                SizedBox(width: width, child: cardBuilder(row[c])),
                if (c < row.length - 1) const SizedBox(width: _cardSpacing),
              ],
            ],
          );
          return stretch ? IntrinsicHeight(child: cards) : cards;
        }

        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            for (var r = 0; r < rows.length; r++) ...[
              rowOf(rows[r]),
              if (r < rows.length - 1) const SizedBox(height: _cardSpacing),
            ],
          ],
        );
      },
    );
  }
}

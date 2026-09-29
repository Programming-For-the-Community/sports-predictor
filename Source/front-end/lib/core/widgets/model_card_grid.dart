import 'dart:math' as math;

import 'package:flutter/rendering.dart';
import 'package:flutter/widgets.dart';

const _minCardWidth = 420.0;
const _cardSpacing = 20.0;

/// Lays model cards out in rows of as many columns as fit at `minCardWidth`,
/// each card stretched to fill the row's width -- a single full-width column
/// on a phone. With more than one column, every card in a row takes the
/// tallest one's height (see _EqualHeightRow). Shared by the Models and
/// Performance tabs.
class ModelCardGrid<T> extends StatelessWidget {
  const ModelCardGrid({
    super.key,
    required this.items,
    required this.cardBuilder,
    this.minCardWidth = _minCardWidth,
  });

  final List<T> items;
  final Widget Function(T item) cardBuilder;

  /// The narrowest a card gets before the grid drops a column.
  final double minCardWidth;

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final available = constraints.maxWidth;
        final perRow = ((available + _cardSpacing) / (minCardWidth + _cardSpacing)).floor().clamp(1, 999);
        final width = (available - (perRow - 1) * _cardSpacing) / perRow;

        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: _withGaps(
            _chunk(perRow).map((row) => _row(row, width, perRow)),
            const SizedBox(height: _cardSpacing),
          ),
        );
      },
    );
  }

  Widget _row(List<T> row, double width, int perRow) {
    final cards = [for (final item in row) cardBuilder(item)];
    // A single column has nothing to equalize against.
    if (perRow == 1) return SizedBox(width: width, child: cards.single);
    return _EqualHeightRow(cardWidth: width, children: cards);
  }

  /// `items` split into consecutive rows of `perRow` (the last may be shorter).
  List<List<T>> _chunk(int perRow) => [
        for (var i = 0; i < items.length; i += perRow) items.sublist(i, (i + perRow).clamp(0, items.length)),
      ];

  /// `children` with `gap` between each pair.
  static List<Widget> _withGaps(Iterable<Widget> children, Widget gap) => [
        for (final (i, child) in children.indexed) ...[if (i > 0) gap, child],
      ];
}

/// Cards side by side at a fixed width, all as tall as the tallest. Each card
/// is laid out at its natural height first, then again at the row's height --
/// unlike IntrinsicHeight, this works for cards whose layout depends on their
/// width (a LayoutBuilder). A card sees a bounded height only on that second
/// pass, which is how ModelCardFrame knows to push its footer to the bottom.
class _EqualHeightRow extends MultiChildRenderObjectWidget {
  const _EqualHeightRow({required this.cardWidth, required super.children});

  final double cardWidth;

  @override
  RenderObject createRenderObject(BuildContext context) => _RenderEqualHeightRow(cardWidth);

  @override
  void updateRenderObject(BuildContext context, _RenderEqualHeightRow renderObject) {
    renderObject.cardWidth = cardWidth;
  }
}

class _EqualHeightRowParentData extends ContainerBoxParentData<RenderBox> {}

class _RenderEqualHeightRow extends RenderBox
    with ContainerRenderObjectMixin<RenderBox, _EqualHeightRowParentData>, RenderBoxContainerDefaultsMixin<RenderBox, _EqualHeightRowParentData> {
  _RenderEqualHeightRow(this._cardWidth);

  double _cardWidth;
  double get cardWidth => _cardWidth;
  set cardWidth(double value) {
    if (value == _cardWidth) return;
    _cardWidth = value;
    markNeedsLayout();
  }

  @override
  void setupParentData(RenderBox child) {
    if (child.parentData is! _EqualHeightRowParentData) child.parentData = _EqualHeightRowParentData();
  }

  @override
  void performLayout() {
    var height = 0.0;
    for (var child = firstChild; child != null; child = childAfter(child)) {
      child.layout(BoxConstraints.tightFor(width: cardWidth), parentUsesSize: true);
      height = math.max(height, child.size.height);
    }
    var x = 0.0;
    for (var child = firstChild; child != null; child = childAfter(child)) {
      child.layout(BoxConstraints.tightFor(width: cardWidth, height: height), parentUsesSize: true);
      (child.parentData! as _EqualHeightRowParentData).offset = Offset(x, 0);
      x += cardWidth + _cardSpacing;
    }
    size = constraints.constrain(Size(math.max(0, x - _cardSpacing), height));
  }

  @override
  void paint(PaintingContext context, Offset offset) => defaultPaint(context, offset);

  @override
  bool hitTestChildren(BoxHitTestResult result, {required Offset position}) =>
      defaultHitTestChildren(result, position: position);
}

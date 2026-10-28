import 'package:flutter/material.dart';

/// A bracket wider than the screen, scrolled sideways, with its horizontal
/// scrollbar right below it. The bracket renders at its full height inline,
/// so the page's own vertical scroll is the only one -- the round odds and
/// anything else below it scroll with the bracket.
class HorizontalScrollableBracket extends StatefulWidget {
  const HorizontalScrollableBracket({super.key, required this.width, required this.height, required this.child});

  final double width;
  final double height;
  final Widget child;

  @override
  State<HorizontalScrollableBracket> createState() => _HorizontalScrollableBracketState();
}

class _HorizontalScrollableBracketState extends State<HorizontalScrollableBracket> {
  // Real, interactive horizontal scroll of the bracket content itself.
  final _contentController = ScrollController();
  // The bar's own horizontal scroll -- a second real Scrollable so its
  // Scrollbar thumb is directly draggable, kept in sync with
  // _contentController by a bidirectional listener (sharing one
  // ScrollController between two visible Scrollables doesn't sync drags).
  final _barController = ScrollController();
  bool _syncingHorizontal = false;

  static const double _barHeight = 14;
  static const double _barGap = 8;

  @override
  void initState() {
    super.initState();
    _contentController.addListener(() => _syncHorizontal(from: _contentController, to: _barController));
    _barController.addListener(() => _syncHorizontal(from: _barController, to: _contentController));
  }

  void _syncHorizontal({required ScrollController from, required ScrollController to}) {
    if (_syncingHorizontal || !from.hasClients || !to.hasClients) return;
    final target = from.offset.clamp(0.0, to.position.maxScrollExtent);
    if (target == to.offset) return;
    _syncingHorizontal = true;
    to.jumpTo(target);
    _syncingHorizontal = false;
  }

  @override
  void dispose() {
    _contentController.dispose();
    _barController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final content = SingleChildScrollView(
          controller: _contentController,
          scrollDirection: Axis.horizontal,
          child: SizedBox(width: widget.width, height: widget.height, child: widget.child),
        );
        // No bar for a bracket that already fits across.
        if (widget.width <= constraints.maxWidth) return content;
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            content,
            const SizedBox(height: _barGap),
            Scrollbar(
              controller: _barController,
              thumbVisibility: true,
              child: SingleChildScrollView(
                controller: _barController,
                scrollDirection: Axis.horizontal,
                child: SizedBox(width: widget.width, height: _barHeight),
              ),
            ),
          ],
        );
      },
    );
  }
}

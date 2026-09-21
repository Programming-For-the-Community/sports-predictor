import 'package:flutter/material.dart';

/// One parent widget holding both the scrollable bracket and its
/// horizontal scrollbar, with the bar genuinely pinned to this widget's
/// own bottom edge -- fixed there regardless of how far the bracket is
/// scrolled vertically, rather than a Scrollbar's usual behavior of
/// sitting at the bottom edge of its own (possibly several-thousand-px-
/// tall) content. A card can legitimately scroll behind the bar as a
/// result (the bar is a real overlay, drawn on top, not part of the
/// vertical scroll flow) -- accepted trade-off for keeping the bar
/// reachable without first scrolling the whole bracket down to its own
/// natural end.
///
/// Only kicks in once `height` (the bracket's real content height)
/// actually exceeds a viewport-relative cap: a bracket that already fits
/// in one screenful renders as a plain Column (content, then the bar
/// right below it) with no inner scroll region or overlay at all, since
/// there's nothing for a card to scroll behind in the first place.
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
  // The overlay bar's own horizontal scroll -- a second real Scrollable
  // (not just a repaint of _contentController's position) so its own
  // Scrollbar thumb is directly draggable, kept in sync with
  // _contentController by a plain bidirectional listener (sharing one
  // ScrollController between two simultaneously-visible Scrollables does
  // NOT sync drag gestures on its own -- only that controller's own
  // jumpTo/animateTo calls reach every attached position).
  final _barController = ScrollController();
  final _verticalController = ScrollController();
  bool _syncingHorizontal = false;

  // A cap, not a fixed size -- a bracket shorter than this (most
  // conference tournaments, NFL/NBA's own non-March-Madness brackets)
  // renders with no inner scroll region/overlay at all (see build()).
  // _maxPaneHeight is a ceiling on top of the fraction, not a target --
  // it used to sit at 720px, which is SHORTER than a typical combined
  // NFL conference bracket's own natural height (~900px), so every NFL
  // bracket tripped the capped-pane path unconditionally regardless of
  // how tall the real viewport was (a real complaint 2026-09-xx: a large
  // screen still forced an inner vertical scroll for a bracket that
  // would otherwise have fit). Raised well above every conference-style
  // bracket's real height so only genuinely oversized ones (March
  // Madness) still get the capped, pinned-scrollbar treatment.
  static const double _maxPaneHeightFraction = 0.8;
  static const double _minPaneHeight = 360;
  static const double _maxPaneHeight = 1600;
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
    _verticalController.dispose();
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

        // The overlay scrollbar is only worth showing (and reserving
        // _barHeight + _barGap of vertical space for) when the bracket
        // is actually wider than the space it has to render in --
        // previously always rendered regardless, so a bracket narrow
        // enough to need no horizontal scroll at all (most conference
        // brackets on a normal-or-wider viewport) still showed an inert,
        // permanently-full-width bar under it that did nothing --
        // a real complaint 2026-09-xx ("placeholder bar").
        final needsHorizontalScroll = widget.width > constraints.maxWidth;
        final bar = needsHorizontalScroll
            ? Scrollbar(
                controller: _barController,
                thumbVisibility: true,
                child: SingleChildScrollView(
                  controller: _barController,
                  scrollDirection: Axis.horizontal,
                  child: SizedBox(width: widget.width, height: _barHeight),
                ),
              )
            : null;

        final viewportCap = (MediaQuery.sizeOf(context).height * _maxPaneHeightFraction).clamp(_minPaneHeight, _maxPaneHeight);
        if (widget.height <= viewportCap) {
          return Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [content, if (bar != null) ...[const SizedBox(height: _barGap), bar]],
          );
        }

        return SizedBox(
          height: viewportCap,
          child: Stack(
            children: [
              Positioned.fill(
                bottom: bar != null ? _barHeight + _barGap : 0,
                child: SingleChildScrollView(controller: _verticalController, child: content),
              ),
              if (bar != null) Positioned(left: 0, right: 0, bottom: 0, child: bar),
            ],
          ),
        );
      },
    );
  }
}

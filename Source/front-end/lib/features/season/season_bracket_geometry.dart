import 'package:flutter/material.dart';

/// A resolved straight-line piece of a connector, in absolute pixel
/// coordinates -- shared by _BracketConnectorPainter (single left-to-right
/// round mapping) and _MarchMadnessGrid's own painter (two halves, one
/// mirrored, so its segments are resolved to coordinates upfront instead).
class GridSegment {
  const GridSegment(this.from, this.to, this.dashed);
  final Offset from;
  final Offset to;
  final bool dashed;
}

List<GridSegment> elbow(Offset from, Offset to, {required bool dashed}) {
  final midX = (from.dx + to.dx) / 2;
  return [
    GridSegment(from, Offset(midX, from.dy), dashed),
    GridSegment(Offset(midX, from.dy), Offset(midX, to.dy), dashed),
    GridSegment(Offset(midX, to.dy), to, dashed),
  ];
}

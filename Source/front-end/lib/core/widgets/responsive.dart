/// Caps a fixed "ideal" card width down to whatever room is actually
/// available -- used everywhere a Wrap/Row lays out cards at a fixed
/// width (home_page.dart, season_page.dart, model_cards_page.dart) so a
/// card narrower than its own ideal width on a phone-sized viewport
/// shrinks to fit instead of overflowing past the screen edge.
double cardWidth(double idealWidth, double maxWidth) => maxWidth < idealWidth ? maxWidth : idealWidth;

/// As many cards per row as fit at [minWidth], each stretched so the row
/// fills [maxWidth] exactly -- no strip of empty space at the right edge
/// when the room is just short of another column.
double fillCardWidth(double minWidth, double maxWidth, {double spacing = 0}) {
  if (maxWidth <= minWidth) return maxWidth;
  final columns = ((maxWidth + spacing) / (minWidth + spacing)).floor();
  return (maxWidth - spacing * (columns - 1)) / columns;
}

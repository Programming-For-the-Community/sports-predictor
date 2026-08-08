/// Caps a fixed "ideal" card width down to whatever room is actually
/// available -- used everywhere a Wrap/Row lays out cards at a fixed
/// width (home_page.dart, season_page.dart, model_cards_page.dart) so a
/// card narrower than its own ideal width on a phone-sized viewport
/// shrinks to fit instead of overflowing past the screen edge.
double cardWidth(double idealWidth, double maxWidth) => maxWidth < idealWidth ? maxWidth : idealWidth;

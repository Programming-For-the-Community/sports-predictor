/// Shared sizing constants for every bracket-shaped widget on the season
/// page (_BracketTree's single converging tree, _MarchMadnessGrid's own
/// 4-region grid, and the matchup/championship cards both place). A single
/// source of truth instead of each widget defining -- or reaching across
/// into another widget's -- its own copy.
abstract final class BracketDimensions {
  static const double cardWidth = 220;
  static const double cardHeight = 108;
  static const double roundGap = 40;
  static const double verticalUnit = 124;
  static const double headerHeight = 20;

  // A conference/region label floats above the first card of its own
  // band (see _BracketTree's own conferenceLabels/_MarchMadnessGrid's own
  // regionLabel) -- headerHeight is the wrong amount to reserve for it:
  // that constant sizes the *fixed top round-name header row*, not this
  // floating label, and reusing it here left only (verticalUnit -
  // cardHeight) - headerHeight = 124 - 108 - 20 = -4px of clearance -- the
  // label actually overlapped the previous row's card by 4px. This is
  // sized to comfortably fit one line of AppTextStyles.microLabel.
  static const double labelClearance = 14;

  // Even with labelClearance sized correctly, the ordinary row-to-row gap
  // (verticalUnit - cardHeight = 16px) only leaves ~2px of real margin
  // once the label's own height is subtracted from it -- visually still
  // reads as smushed against the card above. computeConferenceBracketLayout's
  // own conferenceBOffset (where a 2nd conference/region's whole band
  // starts) adds this many extra slot-units on top of the normal 1-slot
  // gap, specifically at that one seam, for real breathing room -- not a
  // verticalUnit change, which would space out every row in every bracket
  // in the app, not just this one seam.
  static const double labelSeamGapSlots = 0.1;

  // The Championship/Super Bowl/National Championship card -- the single
  // matchup every other card in a bracket ultimately feeds -- renders
  // larger than every other card, with a gradient border and a soft glow,
  // so it reads as the destination at a glance. Shared by every sport's
  // _BracketTree (gated by its own highlightFinalMatchup) and by
  // _MarchMadnessGrid's own separate Championship card, which isn't part
  // of any `rounds` list _BracketTree walks.
  static const double championshipScale = 1.2;
  static const double championshipCardWidth = cardWidth * championshipScale;
  static const double championshipCardHeight = cardHeight * championshipScale;

  // Extra horizontal room before the Championship column, on top of the
  // ordinary roundGap -- without it, the connector legs leading into the
  // card (see _BracketConnectorPainter's own championshipShift doc
  // comment) come out only ~9px long each: championshipShift (22px, half
  // the card's own width increase) already eats most of the ordinary 40px
  // gap, leaving little of it for an actually-visible line. This widens
  // the gap itself rather than shrinking the shift, so the card keeps its
  // own full emphasized size.
  static const double championshipEntryGap = 28;
}

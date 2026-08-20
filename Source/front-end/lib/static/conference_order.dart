/// Shared reading order for a "conference" heading -- season_page.dart's
/// standings groups and event_list_page.dart's event-list groups both key
/// off the same backend field (NFL divisions and NCAAFB conferences share
/// one field, never co-occurring in the same response) and want the same
/// priority ordering, so this lives in one place.
///
/// Power 5 first, then Group of 5. Anything not in this list falls through
/// to compareConferenceOrder's own alphabetical-after-known-ones rule.
const kConferenceOrder = [
  'AFC East', 'AFC North', 'AFC South', 'AFC West',
  'NFC East', 'NFC North', 'NFC South', 'NFC West',
  'SEC', 'Big Ten', 'ACC', 'Big 12', 'Pac-12',
  'American Athletic', 'Conference USA', 'Mid-American', 'Mountain West', 'Sun Belt',
];

/// A `Comparator<String>` for sorting conference/division names -- known
/// names sort by their position in kConferenceOrder, unknown names sort
/// alphabetically after every known one.
int compareConferenceOrder(String a, String b) {
  final ai = kConferenceOrder.indexOf(a);
  final bi = kConferenceOrder.indexOf(b);
  if (ai == -1 && bi == -1) return a.compareTo(b);
  if (ai == -1) return 1;
  if (bi == -1) return -1;
  return ai.compareTo(bi);
}

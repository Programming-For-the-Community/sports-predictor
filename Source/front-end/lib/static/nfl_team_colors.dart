import 'package:flutter/material.dart';

import '../core/models/event.dart';

/// Frontend-only, hand-typed real brand colors for NFL's 32 teams, keyed
/// by ESPN's numeric team_id. No other sport has an equivalent table --
/// see teamDisplay below for how everyone else is handled.
class NflTeam {
  const NflTeam(this.abbreviation, this.primary);
  final String abbreviation;
  // Null means "no official color for this team" -- TeamColorDot omits
  // itself entirely rather than guess.
  final Color? primary;
}

const Map<String, NflTeam> kNflTeams = {
  '1': NflTeam('ATL', Color(0xFFA71930)),
  '2': NflTeam('BUF', Color(0xFF00338D)),
  '3': NflTeam('CHI', Color(0xFF0B162A)),
  '4': NflTeam('CIN', Color(0xFFFB4F14)),
  '5': NflTeam('CLE', Color(0xFF311D00)),
  '6': NflTeam('DAL', Color(0xFF041E42)),
  '7': NflTeam('DEN', Color(0xFFFB4F14)),
  '8': NflTeam('DET', Color(0xFF0076B6)),
  '9': NflTeam('GB', Color(0xFF203731)),
  '10': NflTeam('TEN', Color(0xFF4B92DB)),
  '11': NflTeam('IND', Color(0xFF002C5F)),
  '12': NflTeam('KC', Color(0xFFE31837)),
  '13': NflTeam('LV', Color(0xFF000000)),
  '14': NflTeam('LAR', Color(0xFF003594)),
  '15': NflTeam('MIA', Color(0xFF008E97)),
  '16': NflTeam('MIN', Color(0xFF4F2683)),
  '17': NflTeam('NE', Color(0xFF002244)),
  '18': NflTeam('NO', Color(0xFFD3BC8D)),
  '19': NflTeam('NYG', Color(0xFF0B2265)),
  '20': NflTeam('NYJ', Color(0xFF125740)),
  '21': NflTeam('PHI', Color(0xFF004C54)),
  '22': NflTeam('ARI', Color(0xFF97233F)),
  '23': NflTeam('PIT', Color(0xFFFFB612)),
  '24': NflTeam('LAC', Color(0xFF0080C6)),
  '25': NflTeam('SF', Color(0xFFAA0000)),
  '26': NflTeam('SEA', Color(0xFF002244)),
  '27': NflTeam('TB', Color(0xFFD50A0A)),
  '28': NflTeam('WSH', Color(0xFF5A1414)),
  '29': NflTeam('CAR', Color(0xFF0085CA)),
  '30': NflTeam('JAX', Color(0xFF006778)),
  '33': NflTeam('BAL', Color(0xFF241773)),
  '34': NflTeam('HOU', Color(0xFF03202F)),
};

/// Parses a bare 6-digit hex string (ESPN's own format, e.g. "c8102e", no
/// "#") into a Color -- null on anything else (missing, malformed, an
/// already-"#"-prefixed string) so a bad value degrades to "no dot" rather
/// than a thrown FormatException.
Color? _parseApiColor(String? hex) {
  if (hex == null) return null;
  final match = RegExp(r'^#?([0-9a-fA-F]{6})$').firstMatch(hex);
  if (match == null) return null;
  return Color(int.parse('FF${match.group(1)}', radix: 16));
}

/// The one function every team-display widget should call -- prefers the
/// NFL static table above only when sport is 'nfl'; every other sport
/// falls back to `apiColor`. Gated on sport, not just "is entityId in the
/// table": raw ESPN team ids are not unique across sports, so a non-NFL
/// team whose id happens to fall in kNflTeams' range would otherwise
/// render as the wrong NFL team. No hashed placeholder color for a team
/// with neither source -- TeamColorDot omits itself entirely rather than
/// show an arbitrary made-up color. Takes the raw fields rather than a
/// Participant so both event-shaped callers (teamDisplay below) and
/// standings/bracket/cup rows share one rule.
NflTeam teamDisplayFor(String sport, String entityId, String? abbreviation, {String? apiColor}) {
  if (sport == 'nfl') {
    final known = kNflTeams[entityId];
    if (known != null) return known;
  }
  return NflTeam(abbreviation ?? entityId, _parseApiColor(apiColor));
}

/// See Participant.abbreviation/.color for where the fallback values come from.
NflTeam teamDisplay(String sport, Participant participant) =>
    teamDisplayFor(sport, participant.entityId, participant.abbreviation, apiColor: participant.color);

/// An event/tournament/race's own top-level lifecycle state -- shared
/// identically by every sport (head-to-head and field alike): the backend
/// only ever writes 'scheduled' or 'completed' here (see DATA_SCHEMA.md's
/// event.status), distinct from any sport's own per-participant status
/// vocabulary (field_status_pill.dart's PgaParticipantStatus,
/// f1_status_pill.dart's F1DriverStatus) or from BracketMatchupStatus
/// (season_projection.dart), both of which vary by sport/shape. No
/// canonical definition existed for this one before -- every file that
/// needed it independently retyped the raw string.
library;

abstract final class EventStatus {
  static const scheduled = 'scheduled';
  static const completed = 'completed';

  // Named only for readability at a fromJson default site -- same value
  // ('') as before, not a third real status. Means "no status field on
  // this record at all" (an event ingested before the field existed).
  static const unknown = '';
}

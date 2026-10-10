/// A golfer's round status, as library/normalize/pga.py's map_status writes
/// it and field_leaderboard_table.dart words it under their name.
abstract final class PgaParticipantStatus {
  static const scheduled = 'scheduled';
  static const finished = 'finished';
  static const cut = 'cut';
  static const madeCutDidNotFinish = 'made_cut_did_not_finish';
  static const withdrawn = 'withdrawn';
  static const inProgress = 'in_progress';
}

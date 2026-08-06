# EventBridge Scheduler resource that starts the nfl-season-schedule-sync
# state machine (sfn-nfl-season-schedule-sync.tf).
#
# Weekly, Thursday 10:00 UTC -- well before that week's Thursday-night
# kickoff (~00:15 UTC Friday), so the frontend's upcoming-events list and
# season simulation are guaranteed fresh before any game in the coming week
# starts. Idempotent and cheap to re-run (nfl-ingest's own box-score fetch
# is already skip-if-exists, and re-upserting an already-final week's event
# data is a harmless no-op write) -- year-round, not gated to the season,
# since this is exactly what needs to run in the off-season to seed next
# season's schedule ahead of Week 1.
resource "aws_scheduler_schedule" "nfl_season_schedule_sync" {
  name        = "${var.project}-nfl-season-schedule-sync"
  description = "Starts the nfl-season-schedule-sync state machine weekly, Thu 10:00 UTC, year-round."
  group_name  = aws_scheduler_schedule_group.sports_predictor.name

  schedule_expression          = "cron(0 10 ? * THU *)"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_sfn_state_machine.nfl_season_schedule_sync.arn
    role_arn = aws_iam_role.eventbridge_invoke.arn
  }
}

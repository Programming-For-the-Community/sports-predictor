# Invokes the f1_live_scores Lambda every 3 minutes to refresh its
# live-timing cache. Runs unconditionally year-round -- the cheap/
# expensive split (whether any race is actually live right now) lives
# entirely in application code (live_scores.refresh's own state.type.state
# check against ESPN's real response), not here. Slower cadence than
# PGA's own 1-minute poll: F1's single full-season scoreboard call is
# heavier (~25 events x up to 7 competitions each) than PGA's own
# per-tournament leaderboard fetch, and a race's own running order changes
# on the order of seconds, not something a 1-minute cadence would track
# meaningfully better than 3 anyway.
resource "aws_scheduler_schedule" "f1_live_scores" {
  name        = "${var.project}-f1-live-scores"
  description = "Invokes the f1_live_scores Lambda every 3 minutes to refresh its live-timing cache for any F1 race or Sprint session currently live."
  group_name  = aws_scheduler_schedule_group.sports_predictor.name

  schedule_expression = "rate(3 minutes)"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.f1_live_scores.arn
    role_arn = aws_iam_role.eventbridge_invoke.arn

    input = jsonencode({
      detail-type = "LiveScoreRefresh"
    })
  }
}

# Invokes the pga_live_scores Lambda every 1 minute to refresh its
# leaderboard-snapshot cache. Runs unconditionally, every minute,
# year-round -- the cheap/expensive split (whether any tournament is
# actually in its daily poll window) lives entirely in application code
# (live_scores._candidate_events), not here. Bumped from 5 minutes
# 2026-08-28 -- at this Lambda's own size (256MB, lambda-pga-live-scores.tf)
# the 5x invocation increase (~105k/yr -> ~525k/yr) costs low-single-digit
# dollars/year either way (Lambda's request+duration pricing is dominated
# by invocation count here, not duration -- most invocations outside a
# tournament's poll window return almost immediately, one cheap DynamoDB
# read and an early exit, no ESPN call at all).
resource "aws_scheduler_schedule" "pga_live_scores" {
  name        = "${var.project}-pga-live-scores"
  description = "Invokes the pga_live_scores Lambda every minute to refresh its leaderboard-snapshot cache for any tournament in its daily poll window (1h before the day's tee times through 1h after the last active golfer's status went non-active)."
  group_name  = aws_scheduler_schedule_group.sports_predictor.name

  schedule_expression = "rate(1 minute)"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.pga_live_scores.arn
    role_arn = aws_iam_role.eventbridge_invoke.arn

    input = jsonencode({
      detail-type = "LiveScoreRefresh"
    })
  }
}

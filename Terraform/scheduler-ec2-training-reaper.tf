# Invokes the ec2-training-reaper Lambda every 10 minutes, always -- not
# gated to "only while a training run might be active", since the whole
# point is catching orphans left by a manually-stopped or otherwise
# unexpectedly-ended run, which by definition isn't predictable from a
# schedule. Cheap and a no-op when nothing's idle (see the handler's own
# early-return when there are no tag-matched running instances at all).
resource "aws_scheduler_schedule" "ec2_training_reaper" {
  name        = "${var.project}-ec2-training-reaper"
  description = "Invokes the ec2-training-reaper Lambda every 10 minutes to terminate any idle EC2 training instance a training run left orphaned."
  group_name  = aws_scheduler_schedule_group.sports_predictor.name

  schedule_expression = "rate(10 minutes)"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.ec2_training_reaper.arn
    role_arn = aws_iam_role.eventbridge_invoke.arn
  }
}

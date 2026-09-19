# Keeps the shared predict-read Lambda warm. Only real API Gateway
# requests reach predict-read, so a ping every 5 minutes, comfortably
# inside Lambda's idle-reclaim window, keeps at least one warm container
# ready for real visits.
#
# One schedule now, not 6 -- lambda-predict-read.tf's own handler serves
# every sport from the same shared singletons (_get_storage/_get_model_
# bucket/_get_predictions_table construct real resources that are already
# single tables/buckets shared across all 6 sports, see Terraform/
# locals.tf), so one ping warms all of them at once. Known trade-off: a
# burst of concurrent cross-sport traffic can now cold-start more often
# than when each sport had its own dedicated warm container -- accepted,
# not something to solve in Terraform, in exchange for 6 fewer Lambda
# resources.
#
# The handler's lambda_handler checks event["warmup"] before its normal
# resource-based routing, and constructs its lazy singletons without
# touching DynamoDB/S3 itself, so each tick costs one Lambda invocation
# and nothing else.
resource "aws_scheduler_schedule" "predict_read_warmup" {
  name        = "${var.project}-predict-read-warmup"
  description = "Pings the shared predict-read Lambda every 5 minutes to keep a warm execution environment ready for real requests, across all 6 sports."
  group_name  = aws_scheduler_schedule_group.sports_predictor.name

  schedule_expression = "rate(5 minutes)"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.predict_read.arn
    role_arn = aws_iam_role.eventbridge_invoke.arn

    input = jsonencode({
      warmup = true
    })
  }
}

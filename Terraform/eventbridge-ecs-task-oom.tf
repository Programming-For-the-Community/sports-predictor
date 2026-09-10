# Alerts via SNS when any task on the shared cluster is OOM-killed.
resource "aws_cloudwatch_event_rule" "ecs_task_oom" {
  name        = "${var.project}-ecs-task-oom"
  description = "Fires when any task on the shared cluster is stopped for running out of memory."

  event_pattern = jsonencode({
    source      = ["aws.ecs"]
    detail-type = ["ECS Task State Change"]
    detail = {
      clusterArn    = [aws_ecs_cluster.main.arn]
      lastStatus    = ["STOPPED"]
      stoppedReason = [{ wildcard = "*OutOfMemory*" }]
    }
  })

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "observability"
  })
}

resource "aws_cloudwatch_event_target" "ecs_task_oom_to_ops_alerts" {
  rule = aws_cloudwatch_event_rule.ecs_task_oom.name
  arn  = aws_sns_topic.ops_alerts.arn

  input_transformer {
    input_paths = {
      task_definition = "$.detail.taskDefinitionArn"
      task_arn        = "$.detail.taskArn"
      stopped_reason  = "$.detail.stoppedReason"
    }
    input_template = "\"ECS task ran out of memory: <task_definition> (<task_arn>) -- <stopped_reason>\""
  }
}

# EventBridge -> SNS needs an explicit publish grant.
resource "aws_sns_topic_policy" "ops_alerts_allow_eventbridge" {
  arn = aws_sns_topic.ops_alerts.arn

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowEcsTaskOomEventBridgeRule"
        Effect    = "Allow"
        Principal = { Service = "events.amazonaws.com" }
        Action    = "sns:Publish"
        Resource  = aws_sns_topic.ops_alerts.arn
        Condition = {
          ArnEquals = { "aws:SourceArn" = aws_cloudwatch_event_rule.ecs_task_oom.arn }
        }
      }
    ]
  })
}
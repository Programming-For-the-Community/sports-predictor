# EventBridge Scheduler resources that trigger the NFL score model
# training Fargate task once per score target (see
# Terraform/ecs-task-nfl-train-score-model.tf for the task itself, which
# deliberately does NOT bake SCORE_TARGET into its own environment block
# -- that's supplied here, per target, via each schedule's own
# target.input override instead). Same for_each pattern as
# scheduler-nfl-train-player-prop-model.tf.
#
# Reuses aws_iam_role.eventbridge_invoke (iam-eventbridge-invoke.tf) --
# already scoped for ecs:RunTask on ${var.project}-* task definitions
# plus iam:PassRole on aws_iam_role.ecs_pipeline. No new IAM needed.
#
# Same Wednesday 12:00 UTC as every other NFL training schedule -- all
# three score targets read the same event_features.parquet the
# win-probability task does, already finished by that day's 11:00 UTC
# feature-engineering run, and have no dependency on each other or on
# win-probability/player-prop, so EventBridge fires all of them as
# independent, parallel Fargate tasks at the same moment.
locals {
  nfl_score_targets = [
    "margin",
    "home_score",
    "away_score",
  ]
}

resource "aws_scheduler_schedule" "nfl_train_score_model" {
  for_each = toset(local.nfl_score_targets)

  name        = "${var.project}-nfl-train-score-${replace(each.value, "_", "-")}"
  description = "Retrains the NFL ${each.value} score model (Aug-Feb, Wed 12:00 UTC, after that day's feature engineering run)"
  group_name  = aws_scheduler_schedule_group.sports_predictor.name

  schedule_expression          = "cron(0 12 ? 8-12,1-2 WED *)"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_ecs_cluster.main.arn
    role_arn = aws_iam_role.eventbridge_invoke.arn

    # ECS RunTask container override -- picks which score target this
    # specific schedule trains, since the task definition itself
    # intentionally has no SCORE_TARGET of its own (see
    # ecs-task-nfl-train-score-model.tf).
    input = jsonencode({
      containerOverrides = [
        {
          name = "nfl-train-score-model"
          environment = [
            { name = "SCORE_TARGET", value = each.value },
          ]
        }
      ]
    })

    ecs_parameters {
      task_definition_arn = aws_ecs_task_definition.nfl_train_score_model.arn
      launch_type         = "FARGATE"

      network_configuration {
        subnets          = [aws_subnet.public_1.id, aws_subnet.public_2.id, aws_subnet.public_3.id]
        security_groups  = [aws_security_group.fargate_internet_egress.id]
        assign_public_ip = true
      }
    }
  }
}

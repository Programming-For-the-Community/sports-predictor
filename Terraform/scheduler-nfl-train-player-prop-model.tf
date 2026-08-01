# EventBridge Scheduler resources that trigger the NFL player-prop
# training Fargate task once per target stat (see
# Terraform/ecs-task-nfl-train-player-prop-model.tf for the task itself,
# which deliberately does NOT bake TARGET_STAT into its own environment
# block -- that's supplied here, per stat, via each schedule's own
# target.input override instead).
#
# for_each over nfl_player_prop_stats rather than seven near-duplicate
# resources -- adding an eighth stat later is a one-line change to the
# list below, not a new resource block.
#
# Reuses aws_iam_role.eventbridge_invoke (iam-eventbridge-invoke.tf) --
# already scoped for ecs:RunTask on ${var.project}-* task definitions
# plus iam:PassRole on aws_iam_role.ecs_pipeline. No new IAM needed.
#
# Same Wednesday 12:00 UTC as scheduler-nfl-train-model.tf and
# scheduler-nfl-train-score-model.tf -- all eleven NFL training tasks
# (win-probability, the three score targets, and these seven) read
# datasets already finished by that day's 11:00 UTC feature-engineering
# run, and have no dependency on each other, so EventBridge fires all
# eleven as independent, parallel Fargate tasks at the same moment
# rather than a sequential chain.
locals {
  nfl_player_prop_stats = [
    "passing_yards",
    "passing_touchdowns",
    "rushing_yards",
    "rushing_touchdowns",
    "receiving_yards",
    "receiving_touchdowns",
    "defensive_sacks",
  ]
}

resource "aws_scheduler_schedule" "nfl_train_player_prop_model" {
  for_each = toset(local.nfl_player_prop_stats)

  name        = "${var.project}-nfl-train-player-prop-${replace(each.value, "_", "-")}"
  description = "Retrains the NFL ${each.value} player-prop model (Aug-Feb, Wed 12:00 UTC, after that day's feature engineering run)"
  group_name  = aws_scheduler_schedule_group.sports_predictor.name

  schedule_expression          = "cron(0 12 ? 8-12,1-2 WED *)"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_ecs_cluster.main.arn
    role_arn = aws_iam_role.eventbridge_invoke.arn

    # ECS RunTask container override -- picks which stat this specific
    # schedule trains, since the task definition itself intentionally
    # has no TARGET_STAT of its own (see
    # ecs-task-nfl-train-player-prop-model.tf).
    input = jsonencode({
      containerOverrides = [
        {
          name = "nfl-train-player-prop-model"
          environment = [
            { name = "TARGET_STAT", value = each.value },
          ]
        }
      ]
    })

    ecs_parameters {
      task_definition_arn = aws_ecs_task_definition.nfl_train_player_prop_model.arn
      launch_type         = "FARGATE"

      network_configuration {
        subnets          = [aws_subnet.public_1.id, aws_subnet.public_2.id, aws_subnet.public_3.id]
        security_groups  = [aws_security_group.fargate_internet_egress.id]
        assign_public_ip = true
      }
    }
  }
}

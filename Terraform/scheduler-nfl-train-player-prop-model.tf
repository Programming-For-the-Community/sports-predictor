# EventBridge Scheduler resources that trigger the NFL player-prop
# training Fargate task once per target stat (see
# Terraform/ecs-task-nfl-train-player-prop-model.tf for the task itself,
# which deliberately does NOT bake TARGET_STAT into its own environment
# block -- that's supplied here, per stat, via each schedule's own
# target.input override instead).
#
# for_each over nfl_player_prop_stats rather than seven near-duplicate
# resources -- adding an eighth stat later is a one-line change to the
# map below, not a new resource block.
#
# Reuses aws_iam_role.eventbridge_invoke (iam-eventbridge-invoke.tf) --
# already scoped for ecs:RunTask on ${var.project}-* task definitions
# plus iam:PassRole on aws_iam_role.ecs_pipeline. No new IAM needed.
#
# Slots 5-11 of the 11-task, 30-minute stagger described in
# scheduler-nfl-train-win-probability-model.tf (14:00 through 17:00 UTC) -- each stat's
# map value is its own "minute hour" pair, not just the stat name, since
# a set/list for_each has no stable per-item ordering to derive a time
# offset from. Launching every NFL training task at the same instant
# once exceeded the account's Fargate on-demand vCPU quota.
locals {
  nfl_player_prop_stats = {
    "passing_yards"        = "0 14"
    "passing_touchdowns"   = "30 14"
    "rushing_yards"        = "0 15"
    "rushing_touchdowns"   = "30 15"
    "receiving_yards"      = "0 16"
    "receiving_touchdowns" = "30 16"
    "defensive_sacks"      = "0 17"
  }
}

resource "aws_scheduler_schedule" "nfl_train_player_prop_model" {
  for_each = local.nfl_player_prop_stats

  name        = "${var.project}-nfl-train-player-prop-${replace(each.key, "_", "-")}"
  description = "Retrains the NFL ${each.key} player-prop model (Aug-Feb, Wed ${each.value} UTC, staggered after that day's feature engineering run)"
  group_name  = aws_scheduler_schedule_group.sports_predictor.name

  schedule_expression          = "cron(${each.value} ? 8-12,1-2 WED *)"
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
            { name = "TARGET_STAT", value = each.key },
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

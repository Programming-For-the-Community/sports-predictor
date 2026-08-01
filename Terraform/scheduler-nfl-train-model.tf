# EventBridge Scheduler resource that triggers the NFL train-model Fargate
# task (see Terraform/ecs-task-nfl-train-model.tf for the task itself).
# Same shape as scheduler-nfl-feature-engineering.tf -- ECS RunTask target,
# not a Lambda; the target ARN at the top level is the ECS CLUSTER, and
# which task definition to run is specified inside ecs_parameters.
#
# Reuses aws_iam_role.eventbridge_invoke (iam-eventbridge-invoke.tf) --
# already scoped for exactly this: ecs:RunTask on ${var.project}-* task
# definitions, plus iam:PassRole on aws_iam_role.ecs_pipeline, the role
# this task runs as. No new IAM needed.
#
# Network config mirrors feature engineering's for the same reason: no NAT
# Gateway, no ECR VPC Interface Endpoints, so this needs the public
# subnet + fargate_internet_egress security group to pull its image, not
# the tighter ecs_pipeline security group its own comment describes.
#
# Wednesday 12:00 UTC -- one hour after the feature-engineering schedule
# (11:00 UTC, see scheduler-nfl-feature-engineering.tf), so training
# always reads that week's freshly rebuilt event_features.parquet rather
# than racing a still-in-progress feature-engineering run.
#
# This is the first slot in an 11-task, 15-minute stagger across every
# NFL training schedule (this one, the 3 in scheduler-nfl-train-score-model.tf,
# and the 7 in scheduler-nfl-train-player-prop-model.tf) -- launching all
# eleven at the same instant once exceeded the account's Fargate
# on-demand vCPU quota. Spacing them 12:00 through 14:30 keeps at most
# one training task's vCPU allocation in flight at a time.
resource "aws_scheduler_schedule" "nfl_train_model" {
  name        = "${var.project}-nfl-train-model"
  description = "Retrains the NFL win-probability model (Aug-Feb, Wed 12:00 UTC, after that day's feature engineering run)"
  group_name  = aws_scheduler_schedule_group.sports_predictor.name

  schedule_expression          = "cron(0 12 ? 8-12,1-2 WED *)"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_ecs_cluster.main.arn
    role_arn = aws_iam_role.eventbridge_invoke.arn

    ecs_parameters {
      task_definition_arn = aws_ecs_task_definition.nfl_train_model.arn
      launch_type         = "FARGATE"

      network_configuration {
        subnets          = [aws_subnet.public_1.id, aws_subnet.public_2.id, aws_subnet.public_3.id]
        security_groups  = [aws_security_group.fargate_internet_egress.id]
        assign_public_ip = true
      }
    }
  }
}

# EventBridge Scheduler resource that triggers the NFL feature-engineering
# Fargate task (see Terraform/ecs-task-nfl-feature-engineering.tf for the
# task itself). Split into its own file for the same reason as
# scheduler-nfl-ingest.tf -- every sport/component gets its own schedule
# file, sharing the one schedule group in scheduler-group.tf.
#
# Unlike the ingest schedules, this targets ECS RunTask directly rather
# than a Lambda -- EventBridge Scheduler supports both target shapes; the
# ecs_parameters block below is what selects the ECS one. The target ARN
# at the top level is the ECS CLUSTER, not the task definition -- which
# task definition to run is specified inside ecs_parameters.
#
# Reuses aws_iam_role.eventbridge_invoke (iam-eventbridge-invoke.tf) --
# already scoped for exactly this: ecs:RunTask on ${var.project}-* task
# definitions, plus iam:PassRole on aws_iam_role.ecs_pipeline, the role
# this task runs as. No new IAM needed.
#
# Network config (subnets/security group) mirrors what a manual
# backfill/feature-engineering launch would use -- public subnets +
# fargate_internet_egress, not the tighter ecs_pipeline security group;
# see the comment in ecs-task-nfl-feature-engineering.tf for why (no NAT
# Gateway, no ECR VPC Interface Endpoints, so a private subnet can't pull
# this task's image).
#
# Wednesday 11:00 UTC -- one hour after the Wednesday ingest run (10:00
# UTC, see scheduler-nfl-ingest.tf), so this always reads a complete
# week's data rather than racing a still-in-progress ingest run. Ingest
# itself finishes in well under a minute at this data volume (and
# normalize's own fan-out work is a handful of fast DynamoDB writes), so
# one hour is still generous margin, not a tight dependency.
resource "aws_scheduler_schedule" "nfl_feature_engineering" {
  name        = "${var.project}-nfl-feature-engineering"
  description = "Rebuilds NFL training datasets (Aug-Feb, Wed 11:00 UTC, after that day's ingest run)"
  group_name  = aws_scheduler_schedule_group.sports_predictor.name

  schedule_expression          = "cron(0 11 ? 8-12,1-2 WED *)"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_ecs_cluster.main.arn
    role_arn = aws_iam_role.eventbridge_invoke.arn

    ecs_parameters {
      task_definition_arn = aws_ecs_task_definition.nfl_feature_engineering.arn
      launch_type         = "FARGATE"

      network_configuration {
        subnets          = [aws_subnet.public_1.id, aws_subnet.public_2.id, aws_subnet.public_3.id]
        security_groups  = [aws_security_group.fargate_internet_egress.id]
        assign_public_ip = true
      }
    }
  }
}

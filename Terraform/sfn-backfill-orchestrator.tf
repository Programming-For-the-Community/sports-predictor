# Step Functions wrapper around each sport's standalone backfill Fargate
# task (ecs-task-<sport>-backfill.tf), so PropagateTags: TASK_DEFINITION
# is always set (RunTask doesn't do this by default). No EventBridge
# schedule -- start manually:
#   aws stepfunctions start-execution --input '{"sport": "ncaafb", "container_overrides": []}'
# container_overrides is required; use [] for the task definition's own
# defaults, or entries in the {"Name": ..., "Value": ...} shape to
# override, e.g.:
#   {"sport": "ncaafb", "container_overrides": [{"Name": "START_SEASON", "Value": "2024"}]}
resource "aws_cloudwatch_log_group" "backfill_orchestrator" {
  name              = "/aws/vendedlogs/states/${var.project}-backfill-orchestrator"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "orchestration"
  })
}

resource "aws_sfn_state_machine" "backfill_orchestrator" {
  name     = "${var.project}-backfill-orchestrator"
  role_arn = aws_iam_role.stepfunctions_orchestrator.arn
  type     = "STANDARD"

  depends_on = [time_sleep.iam_propagation]

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.backfill_orchestrator.arn}:*"
    include_execution_data = true
    level                  = "ALL"
  }

  definition = <<EOF
{
  "Comment": "Runs one sport's backfill Fargate task with PropagateTags set.",
  "StartAt": "RunBackfillTask",
  "States": {
    "RunBackfillTask": {
      "Type": "Task",
      "Resource": "arn:aws:states:::ecs:runTask.sync",
      "Parameters": {
        "Cluster": "${aws_ecs_cluster.main.arn}",
        "TaskDefinition.$": "States.Format('${var.project}-{}-backfill', $.sport)",
        "LaunchType": "FARGATE",
        "PropagateTags": "TASK_DEFINITION",
        "NetworkConfiguration": {
          "AwsvpcConfiguration": {
            "Subnets": ["${aws_subnet.public_1.id}", "${aws_subnet.public_2.id}", "${aws_subnet.public_3.id}"],
            "SecurityGroups": ["${aws_security_group.fargate_internet_egress.id}"],
            "AssignPublicIp": "ENABLED"
          }
        },
        "Overrides": {
          "ContainerOverrides": [
            {
              "Name.$": "States.Format('{}-backfill', $.sport)",
              "Environment.$": "$.container_overrides"
            }
          ]
        }
      },
      "End": true
    }
  }
}
EOF

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "orchestration"
  })
}
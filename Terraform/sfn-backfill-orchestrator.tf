# On-demand Step Functions wrapper around each sport's standalone backfill
# Fargate task (ecs-task-<sport>-backfill.tf) -- replaces invoking
# `aws ecs run-task` by hand. RunTask does not propagate a task
# definition's own tags to the running task unless the caller explicitly
# passes PropagateTags: TASK_DEFINITION; a human running the raw CLI/
# console command has to remember that every single time, and evidently
# didn't always -- $7.86 of genuinely-ours, otherwise-untagged Fargate
# compute cost turned up in Cost Explorer for August 2026, spread across
# 11 separate days. Routing the launch through Step Functions instead
# makes PropagateTags a property of this state machine's own definition,
# the same fix sfn-training-orchestrator.tf's own RunFeatureEngineering/
# RunTrainingTask/RunTrainingTaskOnDemand states already use, so it can't
# be forgotten again. feature-engineering was never actually at risk of
# this despite ecs-task-*-feature-engineering.tf's own "Standalone Fargate
# task" comment -- that's describing the task definition (no ECS Service
# wraps it), not how it's launched -- sfn-training-orchestrator.tf's own
# RunFeatureEngineering state already runs it with PropagateTags set.
# Backfill had no such state machine at all until now.
#
# No EventBridge schedule -- unlike ingest/training, a backfill run is a
# rare, deliberate, human-triggered action (an initial historical load, or
# recovering a specific gap), not a recurring job. Start an execution by
# hand (console "Start execution", or `aws stepfunctions start-execution`)
# with input like:
#   {"sport": "ncaafb", "container_overrides": []}
# container_overrides is always required in the input -- use [] to run
# with the task definition's own full-historical-run defaults, or supply
# entries to narrow a run, in the same {"Name": ..., "Value": ...} shape
# "ECS Run Task -> Container overrides -> Environment variables" already
# used by hand, e.g.:
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

  # Needs the PassBackfillRoles grant added alongside this in
  # iam-stepfunctions-orchestrator.tf.
  depends_on = [time_sleep.iam_propagation]

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.backfill_orchestrator.arn}:*"
    include_execution_data = true
    level                  = "ALL"
  }

  definition = <<EOF
{
  "Comment": "Runs one sport's standalone backfill Fargate task with PropagateTags guaranteed, in place of a hand-run aws ecs run-task. Started manually -- see this state machine's own Terraform resource comment for the input shape.",
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
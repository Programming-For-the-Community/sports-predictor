# Step Functions orchestrator health dashboard: execution outcomes and
# duration for both registry-driven orchestrators (sfn-ingest-
# orchestrator.tf, sfn-training-orchestrator.tf), built from AWS/States
# namespace metrics.
#
# Only two state machines exist in this project (every sport onboards
# through these same two), so metrics are referenced directly by
# StateMachineArn rather than a SEARCH() prefix the way the Lambda/
# DynamoDB dashboards discover an open-ended, per-sport resource set.
resource "aws_cloudwatch_dashboard" "step_functions" {
  dashboard_name = "${var.project}-step-functions"

  dashboard_body = jsonencode({
    widgets = [
      # --- Row 1: Execution outcomes ---
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          region  = var.region
          title   = "Ingest orchestrator -- executions"
          view    = "timeSeries"
          stacked = false
          period  = 86400 # daily cadence -- see sfn-ingest-orchestrator.tf
          metrics = [
            ["AWS/States", "ExecutionsSucceeded", "StateMachineArn", aws_sfn_state_machine.ingest_orchestrator.arn, { stat = "Sum", label = "Succeeded" }],
            ["AWS/States", "ExecutionsFailed", "StateMachineArn", aws_sfn_state_machine.ingest_orchestrator.arn, { stat = "Sum", label = "Failed" }],
            ["AWS/States", "ExecutionsTimedOut", "StateMachineArn", aws_sfn_state_machine.ingest_orchestrator.arn, { stat = "Sum", label = "Timed out" }],
            ["AWS/States", "ExecutionsAborted", "StateMachineArn", aws_sfn_state_machine.ingest_orchestrator.arn, { stat = "Sum", label = "Aborted" }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 12
        height = 6
        properties = {
          region  = var.region
          title   = "Training orchestrator -- executions"
          view    = "timeSeries"
          stacked = false
          period  = 2592000 # monthly cadence -- see sfn-training-orchestrator.tf
          metrics = [
            ["AWS/States", "ExecutionsSucceeded", "StateMachineArn", aws_sfn_state_machine.training_orchestrator.arn, { stat = "Sum", label = "Succeeded" }],
            ["AWS/States", "ExecutionsFailed", "StateMachineArn", aws_sfn_state_machine.training_orchestrator.arn, { stat = "Sum", label = "Failed" }],
            ["AWS/States", "ExecutionsTimedOut", "StateMachineArn", aws_sfn_state_machine.training_orchestrator.arn, { stat = "Sum", label = "Timed out" }],
            ["AWS/States", "ExecutionsAborted", "StateMachineArn", aws_sfn_state_machine.training_orchestrator.arn, { stat = "Sum", label = "Aborted" }],
          ]
        }
      },

      # --- Row 2: Execution duration ---
      {
        type   = "metric"
        x      = 0
        y      = 6
        width  = 12
        height = 6
        properties = {
          region  = var.region
          title   = "Ingest orchestrator -- execution time (ms)"
          view    = "timeSeries"
          stacked = false
          period  = 86400
          metrics = [
            ["AWS/States", "ExecutionTime", "StateMachineArn", aws_sfn_state_machine.ingest_orchestrator.arn, { stat = "Average", label = "Average" }],
            ["AWS/States", "ExecutionTime", "StateMachineArn", aws_sfn_state_machine.ingest_orchestrator.arn, { stat = "Maximum", label = "Max" }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 6
        width  = 12
        height = 6
        properties = {
          region  = var.region
          title   = "Training orchestrator -- execution time (ms)"
          view    = "timeSeries"
          stacked = false
          period  = 2592000
          metrics = [
            ["AWS/States", "ExecutionTime", "StateMachineArn", aws_sfn_state_machine.training_orchestrator.arn, { stat = "Average", label = "Average" }],
            ["AWS/States", "ExecutionTime", "StateMachineArn", aws_sfn_state_machine.training_orchestrator.arn, { stat = "Maximum", label = "Max" }],
          ]
        }
      },

      # --- Row 3: Started (volume context for the rates above) ---
      {
        type   = "metric"
        x      = 0
        y      = 12
        width  = 24
        height = 6
        properties = {
          region  = var.region
          title   = "Executions started (both orchestrators)"
          view    = "timeSeries"
          stacked = false
          period  = 86400
          metrics = [
            ["AWS/States", "ExecutionsStarted", "StateMachineArn", aws_sfn_state_machine.ingest_orchestrator.arn, { stat = "Sum", label = "Ingest orchestrator" }],
            ["AWS/States", "ExecutionsStarted", "StateMachineArn", aws_sfn_state_machine.training_orchestrator.arn, { stat = "Sum", label = "Training orchestrator" }],
          ]
        }
      },
    ]
  })
}

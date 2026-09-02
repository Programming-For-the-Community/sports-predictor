# Invokes ec2-training-reaper the moment training_orchestrator ends in
# ABORTED, FAILED, or TIMED_OUT -- the one ending sfn-training-orchestrator.
# tf's own in-graph InvokeReaperAfterCompletion state structurally can't
# cover, since a stopped/failed/timed-out execution never reaches its own
# later states at all (see the reaper's own handler.py docstring). Step
# Functions emits this event on the default event bus natively for every
# state machine -- no extra instrumentation needed to produce it.
#
# SUCCEEDED is deliberately excluded -- that path is already covered by
# the in-graph state, which also has the advantage of running after the
# explicit ScaleDown*Capacity states rather than racing them.
resource "aws_cloudwatch_event_rule" "training_orchestrator_terminal" {
  name        = "${var.project}-training-orchestrator-terminal"
  description = "Invokes ec2-training-reaper when training_orchestrator ends in ABORTED, FAILED, or TIMED_OUT."

  event_pattern = jsonencode({
    source      = ["aws.states"]
    detail-type = ["Step Functions Execution Status Change"]
    detail = {
      stateMachineArn = [aws_sfn_state_machine.training_orchestrator.arn]
      status          = ["ABORTED", "FAILED", "TIMED_OUT"]
    }
  })

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "training"
  })
}

resource "aws_cloudwatch_event_target" "training_orchestrator_terminal_reaper" {
  rule = aws_cloudwatch_event_rule.training_orchestrator_terminal.name
  arn  = aws_lambda_function.ec2_training_reaper.arn
}

# EventBridge rule targets invoke a Lambda via a resource-based permission
# on the function itself, not an assumed IAM role -- a different mechanism
# from every other schedule in this project (which go through EventBridge
# Scheduler and aws_iam_role.eventbridge_invoke instead).
resource "aws_lambda_permission" "training_orchestrator_terminal_invokes_reaper" {
  statement_id  = "AllowTrainingOrchestratorTerminalEvent"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ec2_training_reaper.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.training_orchestrator_terminal.arn
}

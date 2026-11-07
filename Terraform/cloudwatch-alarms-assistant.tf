# The assistant's own metric, from the agents' per-turn JSON log lines
# (Source/library/assistant/turn_log.py): SportsPredictor/Assistant Turns,
# by agent and outcome (answered, blocked, quota, failed, ...). The
# assistant dashboard charts it and the alarms below watch it -- alarms
# can't use SEARCH(), so they read this metric with known dimensions.

locals {
  assistant_metric_namespace = "SportsPredictor/Assistant"
}

resource "aws_cloudwatch_log_metric_filter" "assistant_turns" {
  for_each = local.assistant_agents

  name           = "${var.project}-assistant-${each.key}-turns"
  log_group_name = aws_cloudwatch_log_group.assistant_runtime[each.key].name
  pattern        = "{ $.assistant_turn IS TRUE }"

  metric_transformation {
    name      = "Turns"
    namespace = local.assistant_metric_namespace
    value     = "1"
    unit      = "Count"
    dimensions = {
      agent   = "$.agent"
      outcome = "$.outcome"
    }
  }
}

# Critical: an answer failed outright (model, tool or guardrail error).
resource "aws_cloudwatch_metric_alarm" "assistant_failures" {
  alarm_name          = "${var.project}-assistant-failures"
  alarm_description   = "An assistant turn (either agent) failed in the last 5 minutes -- the user saw 'Something went wrong'."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.ops_alerts.arn]

  dynamic "metric_query" {
    for_each = local.assistant_agents
    content {
      id = "failed_${metric_query.key}"
      metric {
        namespace   = local.assistant_metric_namespace
        metric_name = "Turns"
        dimensions  = { agent = metric_query.key, outcome = "failed" }
        period      = 300
        stat        = "Sum"
      }
    }
  }

  metric_query {
    id          = "failed"
    expression  = "SUM([${join(", ", [for agent in keys(local.assistant_agents) : "failed_${agent}"])}])"
    label       = "Failed assistant turns"
    return_data = true
  }

  tags = local.assistant_tags
}

# Warning: a burst of blocked questions -- prompt attacks or abuse.
resource "aws_cloudwatch_metric_alarm" "assistant_blocked_spike" {
  alarm_name          = "${var.project}-assistant-blocked-spike"
  alarm_description   = "More than 20 assistant questions were blocked by a guardrail in an hour -- likely prompt-injection attempts or abuse."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 20
  treat_missing_data  = "notBreaching"

  dynamic "metric_query" {
    for_each = local.assistant_agents
    content {
      id = "blocked_${metric_query.key}"
      metric {
        namespace   = local.assistant_metric_namespace
        metric_name = "Turns"
        dimensions  = { agent = metric_query.key, outcome = "blocked" }
        period      = 3600
        stat        = "Sum"
      }
    }
  }

  metric_query {
    id          = "blocked"
    expression  = "SUM([${join(", ", [for agent in keys(local.assistant_agents) : "blocked_${agent}"])}])"
    label       = "Blocked assistant questions"
    return_data = true
  }

  tags = local.assistant_tags
}

# Warning: a tool Lambda raised (tools normally return their errors to the
# model, so an unhandled error is a bug).
resource "aws_cloudwatch_metric_alarm" "assistant_tool_errors" {
  alarm_name          = "${var.project}-assistant-tool-errors"
  alarm_description   = "An assistant tool Lambda raised an unhandled error in the last 5 minutes."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 0
  treat_missing_data  = "notBreaching"

  metric_query {
    id = "public_tools"
    metric {
      namespace   = "AWS/Lambda"
      metric_name = "Errors"
      dimensions  = { FunctionName = aws_lambda_function.assistant_public_tools.function_name }
      period      = 300
      stat        = "Sum"
    }
  }

  metric_query {
    id = "admin_tools"
    metric {
      namespace   = "AWS/Lambda"
      metric_name = "Errors"
      dimensions  = { FunctionName = aws_lambda_function.assistant_admin_tools.function_name }
      period      = 300
      stat        = "Sum"
    }
  }

  metric_query {
    id          = "tool_errors"
    expression  = "SUM([public_tools, admin_tools])"
    label       = "Assistant tool errors"
    return_data = true
  }

  tags = local.assistant_tags
}

# Warning: a data producer failed, so cost or traffic answers will go stale.
resource "aws_cloudwatch_metric_alarm" "assistant_producer_errors" {
  alarm_name          = "${var.project}-assistant-producer-errors"
  alarm_description   = "The assistant's cost-snapshot or traffic-aggregate Lambda failed in the last hour; admin answers may be stale."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 0
  treat_missing_data  = "notBreaching"

  metric_query {
    id = "cost"
    metric {
      namespace   = "AWS/Lambda"
      metric_name = "Errors"
      dimensions  = { FunctionName = aws_lambda_function.assistant_cost_snapshot.function_name }
      period      = 3600
      stat        = "Sum"
    }
  }

  metric_query {
    id = "traffic"
    metric {
      namespace   = "AWS/Lambda"
      metric_name = "Errors"
      dimensions  = { FunctionName = aws_lambda_function.assistant_traffic_aggregate.function_name }
      period      = 3600
      stat        = "Sum"
    }
  }

  metric_query {
    id          = "producer_errors"
    expression  = "SUM([cost, traffic])"
    label       = "Assistant producer errors"
    return_data = true
  }

  tags = local.assistant_tags
}

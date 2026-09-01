# The audit that prompted this file found ZERO aws_cloudwatch_metric_alarm
# resources anywhere in the stack -- 37 Lambdas, 2 Step Functions state
# machines, 6 DynamoDB tables, and the shared API Gateway all had
# dashboards (cloudwatch-dashboard-*.tf) but nothing that actually pages
# anyone. This file is deliberately NOT exhaustive per-resource coverage --
# see the cost/tiering table in the accompanying chat response for the
# reasoning behind exactly these 18.
#
# Two tiers, by whether alarm_actions is set:
#   Critical -- alarm_actions = [aws_sns_topic.ops_alerts.arn] (sns-ops-
#   alerts.tf), pages var.alert_email.
#   Warning  -- no alarm_actions at all. Exists purely so
#   cloudwatch-dashboard-alerts.tf has a real alarm resource to show a
#   tile for; deliberately never pages anyone.
#
# Lambda Errors is split by pipeline stage, not one blanket alarm, so a
# predict-path failure (user-facing) can page while an ingest failure
# (self-heals on tomorrow's run) doesn't. predict/predict-read use
# explicit per-function metric_query blocks rather than a SEARCH prefix --
# "sports-predictor-nfl-predict" is a literal PREFIX of "sports-predictor-
# nfl-predict-read", so a SEARCH term of "predict" alone would silently
# pull predict-read's own errors into the predict-only alarm (and vice
# versa) -- exactly the SEARCH-prefix ambiguity class the Lambda-
# observability-dashboard root-cause already surfaced once in this repo.
# The 4 Warning-tier stages (ingest/normalize/live-scores/schedule-sync)
# have no such sibling-name collision, so SEARCH stays safe and much
# shorter there.
locals {
  alarm_all_sports = ["nfl", "ncaafb", "nba", "ncaambb", "pga", "f1"]
}

# ── 1-2. Predict / predict-read Lambda Errors (Critical) ────────────────────

resource "aws_cloudwatch_metric_alarm" "predict_errors" {
  alarm_name          = "${var.project}-predict-lambda-errors"
  alarm_description   = "Any predict Lambda (any sport) errored in the last 5 minutes -- the heavier compute path behind a cache miss."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.ops_alerts.arn]

  dynamic "metric_query" {
    for_each = local.alarm_all_sports
    content {
      id          = "m${index(local.alarm_all_sports, metric_query.value) + 1}"
      return_data = false
      metric {
        metric_name = "Errors"
        namespace   = "AWS/Lambda"
        period      = 300
        stat        = "Sum"
        dimensions = {
          FunctionName = "${var.project}-${metric_query.value}-predict"
        }
      }
    }
  }

  metric_query {
    id          = "total"
    expression  = "m1+m2+m3+m4+m5+m6"
    label       = "Total predict Errors"
    return_data = true
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "observability"
  })
}

resource "aws_cloudwatch_metric_alarm" "predict_read_errors" {
  alarm_name          = "${var.project}-predict-read-lambda-errors"
  alarm_description   = "Any predict-read Lambda (any sport) errored in the last 5 minutes -- the user-facing cache-read path the frontend calls directly."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.ops_alerts.arn]

  dynamic "metric_query" {
    for_each = local.alarm_all_sports
    content {
      id          = "m${index(local.alarm_all_sports, metric_query.value) + 1}"
      return_data = false
      metric {
        metric_name = "Errors"
        namespace   = "AWS/Lambda"
        period      = 300
        stat        = "Sum"
        dimensions = {
          FunctionName = "${var.project}-${metric_query.value}-predict-read"
        }
      }
    }
  }

  metric_query {
    id          = "total"
    expression  = "m1+m2+m3+m4+m5+m6"
    label       = "Total predict-read Errors"
    return_data = true
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "observability"
  })
}

# ── 3-6. Ingest / normalize / live-scores / schedule-sync Errors (Warning) ──

resource "aws_cloudwatch_metric_alarm" "ingest_errors" {
  alarm_name          = "${var.project}-ingest-lambda-errors"
  alarm_description   = "Any ingest Lambda (any sport) errored in the last 5 minutes. Self-heals on tomorrow's run -- dashboard-visible, not paged."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 0
  treat_missing_data  = "notBreaching"

  metric_query {
    id          = "total"
    expression  = "SUM(SEARCH('{AWS/Lambda,FunctionName} Errors ${var.project}- ingest', 'Sum', 300))"
    label       = "Total ingest Errors"
    return_data = true
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "observability"
  })
}

resource "aws_cloudwatch_metric_alarm" "normalize_errors" {
  alarm_name          = "${var.project}-normalize-lambda-errors"
  alarm_description   = "Any normalize Lambda (any sport) errored in the last 5 minutes."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 0
  treat_missing_data  = "notBreaching"

  metric_query {
    id          = "total"
    expression  = "SUM(SEARCH('{AWS/Lambda,FunctionName} Errors ${var.project}- normalize', 'Sum', 300))"
    label       = "Total normalize Errors"
    return_data = true
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "observability"
  })
}

resource "aws_cloudwatch_metric_alarm" "live_scores_errors" {
  alarm_name          = "${var.project}-live-scores-lambda-errors"
  alarm_description   = "Any live-scores Lambda (any sport) errored in the last 5 minutes."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 0
  treat_missing_data  = "notBreaching"

  metric_query {
    id          = "total"
    expression  = "SUM(SEARCH('{AWS/Lambda,FunctionName} Errors ${var.project}- live-scores', 'Sum', 300))"
    label       = "Total live-scores Errors"
    return_data = true
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "observability"
  })
}

resource "aws_cloudwatch_metric_alarm" "schedule_sync_errors" {
  alarm_name          = "${var.project}-schedule-sync-lambda-errors"
  alarm_description   = "Any schedule-sync Lambda (any sport except F1, which has none) errored in the last 5 minutes."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 0
  treat_missing_data  = "notBreaching"

  metric_query {
    id          = "total"
    expression  = "SUM(SEARCH('{AWS/Lambda,FunctionName} Errors ${var.project}- schedule-sync', 'Sum', 300))"
    label       = "Total schedule-sync Errors"
    return_data = true
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "observability"
  })
}

# ── 7. Lambda Throttles, aggregate across all 37 functions (Warning) ────────

resource "aws_cloudwatch_metric_alarm" "lambda_throttles" {
  alarm_name          = "${var.project}-lambda-throttles"
  alarm_description   = "Any Lambda (any sport, any stage) got throttled in the last 5 minutes -- a concurrency-limit symptom."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 0
  treat_missing_data  = "notBreaching"

  metric_query {
    id          = "total"
    expression  = "SUM(SEARCH('{AWS/Lambda,FunctionName} Throttles ${var.project}-', 'Sum', 300))"
    label       = "Total Throttles"
    return_data = true
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "observability"
  })
}

# ── 8. Predict + predict-read Duration p99 (Warning) ─────────────────────────
# Deliberately covers both -- see this file's own top comment on why a
# SEARCH term can't isolate "predict" from "predict-read" the way the
# explicit metric_query alarms above do. Fine here: this is latency
# awareness, not routing, and both stages sharing one Warning-tier signal
# is a reasonable simplification for a non-paging alarm.

resource "aws_cloudwatch_metric_alarm" "predict_path_duration_p99" {
  alarm_name          = "${var.project}-predict-path-duration-p99"
  alarm_description   = "p99 duration across predict + predict-read Lambdas (any sport) exceeded 80% of their configured timeout."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  threshold           = 24000 # ms -- 80% of the 30s timeout every predict/predict-read Lambda shares
  treat_missing_data  = "notBreaching"

  metric_query {
    id          = "total"
    expression  = "SEARCH('{AWS/Lambda,FunctionName} Duration ${var.project}- predict', 'p99', 300)"
    label       = "predict/predict-read Duration p99"
    return_data = true
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "observability"
  })
}

# ── 9-10. DynamoDB throttles / system errors, aggregate across 6 tables ─────
# SEARCH by TableName alone (no Operation dimension specified) matches
# every per-operation series CloudWatch actually stores and sums across
# all of them -- a standard partial-dimension SEARCH, not an approximation.

resource "aws_cloudwatch_metric_alarm" "dynamodb_throttles" {
  alarm_name          = "${var.project}-dynamodb-throttles"
  alarm_description   = "Any of the 6 DynamoDB tables throttled a read or write in the last 5 minutes."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 0
  treat_missing_data  = "notBreaching"

  metric_query {
    id          = "read_throttle"
    expression  = "SUM(SEARCH('{AWS/DynamoDB,TableName} ReadThrottleEvents ${var.project}', 'Sum', 300))"
    return_data = false
  }
  metric_query {
    id          = "write_throttle"
    expression  = "SUM(SEARCH('{AWS/DynamoDB,TableName} WriteThrottleEvents ${var.project}', 'Sum', 300))"
    return_data = false
  }
  metric_query {
    id          = "total"
    expression  = "read_throttle + write_throttle"
    label       = "Total DynamoDB throttle events"
    return_data = true
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "observability"
  })
}

resource "aws_cloudwatch_metric_alarm" "dynamodb_system_errors" {
  alarm_name          = "${var.project}-dynamodb-system-errors"
  alarm_description   = "Any of the 6 DynamoDB tables returned a SystemError (AWS-side fault, not a client throttle) in the last 5 minutes."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.ops_alerts.arn]

  metric_query {
    id          = "total"
    expression  = "SUM(SEARCH('{AWS/DynamoDB,TableName} SystemErrors ${var.project}', 'Sum', 300))"
    label       = "Total DynamoDB SystemErrors"
    return_data = true
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "observability"
  })
}

# ── 11-14. Step Functions execution health ───────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "training_orchestrator_failed" {
  alarm_name          = "${var.project}-training-orchestrator-executions-failed"
  alarm_description   = "The monthly training orchestrator's own execution failed (not an individual target -- TrainAllTargets tolerates those; this is the top-level state machine itself)."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.ops_alerts.arn]
  namespace           = "AWS/States"
  metric_name         = "ExecutionsFailed"
  period              = 300
  statistic           = "Sum"
  dimensions = {
    StateMachineArn = aws_sfn_state_machine.training_orchestrator.arn
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "observability"
  })
}

resource "aws_cloudwatch_metric_alarm" "ingest_orchestrator_failed" {
  alarm_name          = "${var.project}-ingest-orchestrator-executions-failed"
  alarm_description   = "The daily ingest orchestrator's own execution failed."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 0
  treat_missing_data  = "notBreaching"
  namespace           = "AWS/States"
  metric_name         = "ExecutionsFailed"
  period              = 300
  statistic           = "Sum"
  dimensions = {
    StateMachineArn = aws_sfn_state_machine.ingest_orchestrator.arn
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "observability"
  })
}

resource "aws_cloudwatch_metric_alarm" "training_orchestrator_timed_out" {
  alarm_name          = "${var.project}-training-orchestrator-executions-timed-out"
  alarm_description   = "The training orchestrator's own execution timed out."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.ops_alerts.arn]
  namespace           = "AWS/States"
  metric_name         = "ExecutionsTimedOut"
  period              = 300
  statistic           = "Sum"
  dimensions = {
    StateMachineArn = aws_sfn_state_machine.training_orchestrator.arn
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "observability"
  })
}

resource "aws_cloudwatch_metric_alarm" "ingest_orchestrator_timed_out" {
  alarm_name          = "${var.project}-ingest-orchestrator-executions-timed-out"
  alarm_description   = "The ingest orchestrator's own execution timed out."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 0
  treat_missing_data  = "notBreaching"
  namespace           = "AWS/States"
  metric_name         = "ExecutionsTimedOut"
  period              = 300
  statistic           = "Sum"
  dimensions = {
    StateMachineArn = aws_sfn_state_machine.ingest_orchestrator.arn
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "observability"
  })
}

# ── 15-17. API Gateway ───────────────────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "api_gateway_5xx" {
  alarm_name          = "${var.project}-api-gateway-5xx"
  alarm_description   = "The shared API Gateway returned a 5XX in the last 5 minutes."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.ops_alerts.arn]
  namespace           = "AWS/ApiGateway"
  metric_name         = "5XXError"
  period              = 300
  statistic           = "Sum"
  dimensions = {
    ApiName = aws_api_gateway_rest_api.main.name
    Stage   = aws_api_gateway_stage.main.stage_name
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "observability"
  })
}

resource "aws_cloudwatch_metric_alarm" "api_gateway_4xx_rate" {
  alarm_name          = "${var.project}-api-gateway-4xx-rate"
  alarm_description   = "More than 20% of API Gateway requests in the last 5 minutes were 4XX -- could mean a broken frontend auth flow or a client bug, not necessarily an outage."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 20
  treat_missing_data  = "notBreaching"

  metric_query {
    id = "err4xx"
    metric {
      metric_name = "4XXError"
      namespace   = "AWS/ApiGateway"
      period      = 300
      stat        = "Sum"
      dimensions = {
        ApiName = aws_api_gateway_rest_api.main.name
        Stage   = aws_api_gateway_stage.main.stage_name
      }
    }
    return_data = false
  }
  metric_query {
    id = "count"
    metric {
      metric_name = "Count"
      namespace   = "AWS/ApiGateway"
      period      = 300
      stat        = "Sum"
      dimensions = {
        ApiName = aws_api_gateway_rest_api.main.name
        Stage   = aws_api_gateway_stage.main.stage_name
      }
    }
    return_data = false
  }
  metric_query {
    id          = "rate"
    expression  = "(err4xx / count) * 100"
    label       = "4XX rate (%)"
    return_data = true
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "observability"
  })
}

resource "aws_cloudwatch_metric_alarm" "api_gateway_latency_p99" {
  alarm_name          = "${var.project}-api-gateway-latency-p99"
  alarm_description   = "API Gateway p99 latency exceeded 3s in the last 5 minutes."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  threshold           = 3000 # ms
  treat_missing_data  = "notBreaching"

  metric_query {
    id = "latency"
    metric {
      metric_name = "Latency"
      namespace   = "AWS/ApiGateway"
      period      = 300
      stat        = "p99"
      dimensions = {
        ApiName = aws_api_gateway_rest_api.main.name
        Stage   = aws_api_gateway_stage.main.stage_name
      }
    }
    return_data = true
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "observability"
  })
}

# ── 18. CloudFront 5xx error rate ────────────────────────────────────────────
# AWS/CloudFront distribution-level metrics are only ever published to
# us-east-1's CloudWatch, regardless of var.region -- same reason
# waf-cloudfront.tf's own resources use this provider alias.

resource "aws_cloudwatch_metric_alarm" "cloudfront_5xx_rate" {
  provider            = aws.us_east_1
  alarm_name          = "${var.project}-cloudfront-5xx-rate"
  alarm_description   = "CloudFront's 5xx error rate exceeded 1% in the last 5 minutes."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 1
  treat_missing_data  = "notBreaching"
  namespace           = "AWS/CloudFront"
  metric_name         = "5xxErrorRate"
  period              = 300
  statistic           = "Average"
  dimensions = {
    DistributionId = aws_cloudfront_distribution.main.id
    Region         = "Global"
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "observability"
  })
}

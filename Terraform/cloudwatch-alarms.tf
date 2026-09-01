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
# Every aggregate alarm below (Lambda Errors/Throttles by stage, predict-
# path Duration, DynamoDB throttles/SystemErrors) uses explicit per-
# resource metric_query blocks summed/combined via an expression, NOT
# SEARCH() -- a real apply confirmed CloudWatch's PutMetricAlarm flatly
# rejects SEARCH() ("ValidationError: SEARCH is not supported on Metric
# Alarms"), even though the identical SEARCH() expression works fine in a
# dashboard widget (cloudwatch-dashboard-*.tf uses it that way). Lambda
# Errors is further split by pipeline stage, not one blanket alarm, so a
# predict-path failure (user-facing) can page while an ingest failure
# (self-heals on tomorrow's run) doesn't.
locals {
  alarm_all_sports = ["nfl", "ncaafb", "nba", "ncaambb", "pga", "f1"]
  # f1 has no schedule-sync Lambda (see lambda-f1-*.tf's own set) -- every
  # other stage covers all 6 sports.
  alarm_schedule_sync_sports = [for sport in local.alarm_all_sports : sport if sport != "f1"]

  # Every Lambda function name suffix (after "${var.project}-") in the
  # stack -- 37 total (6 sports x 5 stages [ingest/normalize/live-scores/
  # predict/predict-read] + 5 sports' schedule-sync [not f1] + the 2
  # standalone utility Lambdas) -- lambda_throttles' aggregate alarm sums
  # one metric_query per function.
  alarm_all_lambda_suffixes = concat(
    [for sport in local.alarm_all_sports : "${sport}-ingest"],
    [for sport in local.alarm_all_sports : "${sport}-normalize"],
    [for sport in local.alarm_all_sports : "${sport}-live-scores"],
    [for sport in local.alarm_all_sports : "${sport}-predict"],
    [for sport in local.alarm_all_sports : "${sport}-predict-read"],
    [for sport in local.alarm_schedule_sync_sports : "${sport}-schedule-sync"],
    ["season-gate", "cloudwatch-geo-widget"],
  )

  # predict + predict-read together, for predict_path_duration_p99's own
  # combined alarm -- see that resource's own comment for why it covers
  # both stages in one Warning-tier signal.
  alarm_predict_path_suffixes = concat(
    [for sport in local.alarm_all_sports : "${sport}-predict"],
    [for sport in local.alarm_all_sports : "${sport}-predict-read"],
  )

  alarm_dynamodb_tables = [
    aws_dynamodb_table.entities.name,
    aws_dynamodb_table.events.name,
    aws_dynamodb_table.player_game_stats.name,
    aws_dynamodb_table.team_game_stats.name,
    aws_dynamodb_table.predictions.name,
    aws_dynamodb_table.sport_registry.name,
  ]
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

  dynamic "metric_query" {
    for_each = local.alarm_all_sports
    content {
      id          = "m${metric_query.key + 1}"
      return_data = false
      metric {
        metric_name = "Errors"
        namespace   = "AWS/Lambda"
        period      = 300
        stat        = "Sum"
        dimensions = {
          FunctionName = "${var.project}-${metric_query.value}-ingest"
        }
      }
    }
  }

  metric_query {
    id          = "total"
    expression  = join("+", [for i in range(length(local.alarm_all_sports)) : "m${i + 1}"])
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

  dynamic "metric_query" {
    for_each = local.alarm_all_sports
    content {
      id          = "m${metric_query.key + 1}"
      return_data = false
      metric {
        metric_name = "Errors"
        namespace   = "AWS/Lambda"
        period      = 300
        stat        = "Sum"
        dimensions = {
          FunctionName = "${var.project}-${metric_query.value}-normalize"
        }
      }
    }
  }

  metric_query {
    id          = "total"
    expression  = join("+", [for i in range(length(local.alarm_all_sports)) : "m${i + 1}"])
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

  dynamic "metric_query" {
    for_each = local.alarm_all_sports
    content {
      id          = "m${metric_query.key + 1}"
      return_data = false
      metric {
        metric_name = "Errors"
        namespace   = "AWS/Lambda"
        period      = 300
        stat        = "Sum"
        dimensions = {
          FunctionName = "${var.project}-${metric_query.value}-live-scores"
        }
      }
    }
  }

  metric_query {
    id          = "total"
    expression  = join("+", [for i in range(length(local.alarm_all_sports)) : "m${i + 1}"])
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

  dynamic "metric_query" {
    for_each = local.alarm_schedule_sync_sports
    content {
      id          = "m${metric_query.key + 1}"
      return_data = false
      metric {
        metric_name = "Errors"
        namespace   = "AWS/Lambda"
        period      = 300
        stat        = "Sum"
        dimensions = {
          FunctionName = "${var.project}-${metric_query.value}-schedule-sync"
        }
      }
    }
  }

  metric_query {
    id          = "total"
    expression  = join("+", [for i in range(length(local.alarm_schedule_sync_sports)) : "m${i + 1}"])
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

  dynamic "metric_query" {
    for_each = local.alarm_all_lambda_suffixes
    content {
      id          = "m${metric_query.key + 1}"
      return_data = false
      metric {
        metric_name = "Throttles"
        namespace   = "AWS/Lambda"
        period      = 300
        stat        = "Sum"
        dimensions = {
          FunctionName = "${var.project}-${metric_query.value}"
        }
      }
    }
  }

  metric_query {
    id          = "total"
    expression  = join("+", [for i in range(length(local.alarm_all_lambda_suffixes)) : "m${i + 1}"])
    label       = "Total Throttles"
    return_data = true
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "observability"
  })
}

# ── 8. Predict + predict-read Duration p99 (Warning) ─────────────────────────
# Deliberately covers both stages in one alarm -- this is latency
# awareness, not routing, so the predict/predict-read prefix-collision
# concern this file's top comment raises for Errors doesn't apply here.
# MAX() across each function's own p99 series (not SUM/AVG -- durations
# aren't additive) surfaces the worst offender among all 12 functions.

resource "aws_cloudwatch_metric_alarm" "predict_path_duration_p99" {
  alarm_name          = "${var.project}-predict-path-duration-p99"
  alarm_description   = "p99 duration across predict + predict-read Lambdas (any sport) exceeded 80% of their configured timeout."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  threshold           = 24000 # ms -- 80% of the 30s timeout every predict/predict-read Lambda shares
  treat_missing_data  = "notBreaching"

  dynamic "metric_query" {
    for_each = local.alarm_predict_path_suffixes
    content {
      id          = "m${metric_query.key + 1}"
      return_data = false
      metric {
        metric_name = "Duration"
        namespace   = "AWS/Lambda"
        period      = 300
        stat        = "p99"
        dimensions = {
          FunctionName = "${var.project}-${metric_query.value}"
        }
      }
    }
  }

  metric_query {
    id          = "total"
    expression  = "MAX(${join(",", [for i in range(length(local.alarm_predict_path_suffixes)) : "m${i + 1}"])})"
    label       = "predict/predict-read Duration p99 (worst of any function)"
    return_data = true
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "observability"
  })
}

# ── 9-10. DynamoDB throttles / system errors, aggregate across 6 tables ─────

resource "aws_cloudwatch_metric_alarm" "dynamodb_throttles" {
  alarm_name          = "${var.project}-dynamodb-throttles"
  alarm_description   = "Any of the 6 DynamoDB tables throttled a read or write in the last 5 minutes."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 0
  treat_missing_data  = "notBreaching"

  dynamic "metric_query" {
    for_each = local.alarm_dynamodb_tables
    content {
      id          = "read${metric_query.key}"
      return_data = false
      metric {
        metric_name = "ReadThrottleEvents"
        namespace   = "AWS/DynamoDB"
        period      = 300
        stat        = "Sum"
        dimensions = {
          TableName = metric_query.value
        }
      }
    }
  }
  dynamic "metric_query" {
    for_each = local.alarm_dynamodb_tables
    content {
      id          = "write${metric_query.key}"
      return_data = false
      metric {
        metric_name = "WriteThrottleEvents"
        namespace   = "AWS/DynamoDB"
        period      = 300
        stat        = "Sum"
        dimensions = {
          TableName = metric_query.value
        }
      }
    }
  }

  metric_query {
    id = "total"
    expression = join("+", concat(
      [for i in range(length(local.alarm_dynamodb_tables)) : "read${i}"],
      [for i in range(length(local.alarm_dynamodb_tables)) : "write${i}"],
    ))
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

  dynamic "metric_query" {
    for_each = local.alarm_dynamodb_tables
    content {
      id          = "m${metric_query.key + 1}"
      return_data = false
      metric {
        metric_name = "SystemErrors"
        namespace   = "AWS/DynamoDB"
        period      = 300
        stat        = "Sum"
        dimensions = {
          TableName = metric_query.value
        }
      }
    }
  }

  metric_query {
    id          = "total"
    expression  = join("+", [for i in range(length(local.alarm_dynamodb_tables)) : "m${i + 1}"])
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

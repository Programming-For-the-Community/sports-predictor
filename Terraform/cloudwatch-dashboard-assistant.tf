# The assistant at a glance: questions and outcomes (from the agents' turn
# metric, cloudwatch-alarms-assistant.tf), the AgentCore runtimes, model
# tokens, guardrail interventions, the tool and producer Lambdas, Logs
# Insights views over the per-turn JSON lines (turn_log.py), and the
# AgentCore Evaluations scores (agentcore-evaluations.tf).

locals {
  assistant_turn_sources = join(" | ", [for agent in keys(local.assistant_agents) : "SOURCE '${local.assistant_runtime_log_groups[agent]}'"])
  assistant_lambdas = [
    aws_lambda_function.assistant_public_tools.function_name,
    aws_lambda_function.assistant_admin_tools.function_name,
    aws_lambda_function.assistant_cost_snapshot.function_name,
    aws_lambda_function.assistant_traffic_aggregate.function_name,
  ]
}

resource "aws_cloudwatch_dashboard" "assistant" {
  dashboard_name = "${var.project}-assistant"

  dashboard_body = jsonencode({
    widgets = [
      {
        type       = "text", x = 0, y = 0, width = 24, height = 1
        properties = { markdown = "## Questions" }
      },
      {
        type = "metric", x = 0, y = 1, width = 12, height = 6
        properties = {
          region  = var.region, title = "Questions by agent and outcome", view = "timeSeries", stacked = true, period = 3600
          metrics = [[{ expression = "SEARCH('{${local.assistant_metric_namespace},agent,outcome} MetricName=\"Turns\"', 'Sum', 3600)", id = "turns" }]]
        }
      },
      {
        type = "log", x = 12, y = 1, width = 12, height = 6
        properties = {
          region = var.region, title = "Daily users by agent", view = "bar"
          query  = "${local.assistant_turn_sources} | filter ispresent(assistant_turn) and outcome != 'status' | stats count_distinct(user_sub) as users by bin(1d) as day, agent | sort day asc"
        }
      },
      {
        type = "log", x = 0, y = 7, width = 24, height = 6
        properties = {
          region = var.region, title = "Recent blocked, refused and failed questions", view = "table"
          query  = "${local.assistant_turn_sources} | filter ispresent(assistant_turn) and outcome in ['blocked', 'failed', 'quota', 'forbidden'] | fields @timestamp, agent, outcome, question, answer | sort @timestamp desc | limit 50"
        }
      },
      {
        type       = "text", x = 0, y = 13, width = 24, height = 1
        properties = { markdown = "## Runtimes, model and guardrails" }
      },
      {
        type = "metric", x = 0, y = 14, width = 8, height = 6
        properties = {
          region = var.region, title = "Runtime invocations and sessions", view = "timeSeries", period = 300
          metrics = [
            [{ expression = "SEARCH('{AWS/Bedrock-AgentCore} MetricName=\"Invocations\"', 'Sum', 300)", id = "invocations" }],
            [{ expression = "SEARCH('{AWS/Bedrock-AgentCore} MetricName=\"SessionCount\"', 'Sum', 300)", id = "sessions" }],
          ]
        }
      },
      {
        type = "metric", x = 8, y = 14, width = 8, height = 6
        properties = {
          region  = var.region, title = "Runtime latency (ms, end to end)", view = "timeSeries", period = 300
          metrics = [[{ expression = "SEARCH('{AWS/Bedrock-AgentCore} MetricName=\"Latency\"', 'Average', 300)", id = "latency" }]]
        }
      },
      {
        type = "metric", x = 16, y = 14, width = 8, height = 6
        properties = {
          region = var.region, title = "Runtime system errors, user errors and throttles", view = "timeSeries", period = 300
          metrics = [
            [{ expression = "SEARCH('{AWS/Bedrock-AgentCore} MetricName=\"SystemErrors\"', 'Sum', 300)", id = "system" }],
            [{ expression = "SEARCH('{AWS/Bedrock-AgentCore} MetricName=\"UserErrors\"', 'Sum', 300)", id = "user" }],
            [{ expression = "SEARCH('{AWS/Bedrock-AgentCore} MetricName=\"Throttles\"', 'Sum', 300)", id = "throttles" }],
          ]
        }
      },
      {
        type = "metric", x = 0, y = 20, width = 12, height = 6
        properties = {
          region = var.region, title = "Assistant model tokens per day", view = "bar", period = 86400
          metrics = [
            [{ expression = "SEARCH('{AWS/Bedrock,ModelId} MetricName=\"InputTokenCount\" ModelId=\"${local.assistant_model_id}\"', 'Sum', 86400)", id = "input" }],
            [{ expression = "SEARCH('{AWS/Bedrock,ModelId} MetricName=\"OutputTokenCount\" ModelId=\"${local.assistant_model_id}\"', 'Sum', 86400)", id = "output" }],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 20, width = 12, height = 6
        properties = {
          region  = var.region, title = "Guardrail interventions", view = "timeSeries", period = 3600
          metrics = [[{ expression = "SEARCH('{AWS/Bedrock/Guardrails} MetricName=\"InvocationsIntervened\"', 'Sum', 3600)", id = "intervened" }]]
        }
      },
      {
        type       = "text", x = 0, y = 26, width = 24, height = 1
        properties = { markdown = "## Tool and producer Lambdas" }
      },
      {
        type = "metric", x = 0, y = 27, width = 8, height = 6
        properties = {
          region  = var.region, title = "Invocations", view = "timeSeries", period = 300
          metrics = [for fn in local.assistant_lambdas : ["AWS/Lambda", "Invocations", "FunctionName", fn, { stat = "Sum" }]]
        }
      },
      {
        type = "metric", x = 8, y = 27, width = 8, height = 6
        properties = {
          region  = var.region, title = "Errors", view = "timeSeries", period = 300
          metrics = [for fn in local.assistant_lambdas : ["AWS/Lambda", "Errors", "FunctionName", fn, { stat = "Sum" }]]
        }
      },
      {
        type = "metric", x = 16, y = 27, width = 8, height = 6
        properties = {
          region  = var.region, title = "Duration p95 (ms)", view = "timeSeries", period = 300
          metrics = [for fn in local.assistant_lambdas : ["AWS/Lambda", "Duration", "FunctionName", fn, { stat = "p95" }]]
        }
      },
      {
        type       = "text", x = 0, y = 33, width = 24, height = 1
        properties = { markdown = "## Answer quality (AgentCore Evaluations, ${local.assistant_evaluation_sampling_percentage}% of sessions)" }
      },
      {
        type = "metric", x = 0, y = 34, width = 12, height = 6
        properties = {
          region  = var.region, title = "Average score by evaluator (0-1)", view = "timeSeries", period = 86400
          metrics = [[{ expression = "SEARCH('Namespace=\"${local.assistant_evaluation_namespace}\"', 'Average', 86400)", id = "scores" }]]
        }
      },
      {
        type = "log", x = 12, y = 34, width = 12, height = 6
        properties = {
          region = var.region, title = "Recent low scores", view = "table"
          query  = "SOURCE '${aws_cloudwatch_log_group.assistant_evaluations.name}' | fields @timestamp, attributes.gen_ai.evaluation.name as evaluator, attributes.gen_ai.evaluation.score.label as label, attributes.gen_ai.evaluation.score.value as score, attributes.gen_ai.evaluation.explanation as why | filter score < 0.5 | sort @timestamp desc | limit 50"
        }
      },
    ]
  })
}

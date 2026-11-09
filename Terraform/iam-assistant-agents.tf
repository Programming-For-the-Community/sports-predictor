# One role per assistant runtime. The public role can invoke only the public
# tool Lambda, so the admin tools are out of its reach whatever the model
# asks for; the admin role can invoke both. Each role reaches only its own
# guardrail and memory. Tagged with the common tags, so Bedrock model spend
# made through the role carries iamPrincipal/Project for the cost producer.

data "aws_iam_policy_document" "assistant_agent_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.account_id]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:bedrock-agentcore:${var.region}:${var.account_id}:*"]
    }
  }
}

resource "aws_iam_role" "assistant_agent" {
  for_each = local.assistant_agents

  name               = "${var.project}-assistant-${each.key}-runtime"
  assume_role_policy = data.aws_iam_policy_document.assistant_agent_assume.json

  tags = local.assistant_tags
}

locals {
  ecr_repository_name = element(split("/", var.ecr_repo_url), 1)
}

data "aws_iam_policy_document" "assistant_agent" {
  for_each = local.assistant_agents

  statement {
    sid       = "PullAgentImage"
    actions   = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
    resources = ["arn:aws:ecr:${var.region}:${var.account_id}:repository/${local.ecr_repository_name}"]
  }

  statement {
    sid       = "EcrToken"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid       = "RuntimeLogs"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
    resources = ["arn:aws:logs:${var.region}:${var.account_id}:log-group:/aws/bedrock-agentcore/runtimes/*"]
  }

  statement {
    sid       = "Spans"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.region}:${var.account_id}:log-group:aws/spans:*"]
  }

  statement {
    sid       = "Traces"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets"]
    resources = ["*"]
  }

  statement {
    sid       = "AgentMetrics"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["bedrock-agentcore"]
    }
  }

  # The configured model only (InvokeModel also covers Converse): its US
  # inference profile when it has one, and the model in each region the
  # profile can route to.
  statement {
    sid     = "InvokeModel"
    actions = ["bedrock:InvokeModel"]
    resources = concat(
      startswith(local.assistant_model_id, "us.") ? ["arn:aws:bedrock:${var.region}:${var.account_id}:inference-profile/${local.assistant_model_id}"] : [],
      [for r in local.assistant_model_regions : "arn:aws:bedrock:${r}::foundation-model/${local.assistant_foundation_model}"],
    )
  }

  statement {
    sid       = "OwnGuardrail"
    actions   = ["bedrock:ApplyGuardrail"]
    resources = [local.assistant_agents[each.key].guardrail.guardrail_arn]
  }

  statement {
    sid = "OwnMemory"
    actions = [
      "bedrock-agentcore:CreateEvent", "bedrock-agentcore:ListEvents",
      "bedrock-agentcore:ListMemoryRecords", "bedrock-agentcore:BatchDeleteMemoryRecords",
    ]
    resources = [awscc_bedrockagentcore_memory.assistant[each.key].memory_arn]
  }

  statement {
    sid       = "QuotaAndFavorites"
    actions   = ["dynamodb:GetItem", "dynamodb:UpdateItem", "dynamodb:PutItem", "dynamodb:DeleteItem"]
    resources = [aws_dynamodb_table.assistant.arn]
  }

  statement {
    sid       = "ReadSports"
    actions   = ["dynamodb:Scan"]
    resources = [aws_dynamodb_table.sport_registry.arn]
  }

  statement {
    sid     = "InvokeTools"
    actions = ["lambda:InvokeFunction"]
    resources = each.key == "admin" ? [
      aws_lambda_function.assistant_public_tools.arn,
      aws_lambda_function.assistant_admin_tools.arn,
    ] : [aws_lambda_function.assistant_public_tools.arn]
  }
}

resource "aws_iam_role_policy" "assistant_agent" {
  for_each = local.assistant_agents

  name   = "${var.project}-assistant-${each.key}-runtime"
  role   = aws_iam_role.assistant_agent[each.key].id
  policy = data.aws_iam_policy_document.assistant_agent[each.key].json
}

# --- Tool Lambdas -------------------------------------------------------------

data "aws_iam_policy_document" "assistant_tools_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "assistant_public_tools" {
  name               = "${var.project}-lambda-assistant-public-tools-exec"
  assume_role_policy = data.aws_iam_policy_document.assistant_tools_assume.json

  tags = local.assistant_tags
}

resource "aws_iam_role_policy_attachment" "assistant_public_tools_logs" {
  role       = aws_iam_role.assistant_public_tools.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "assistant_public_tools" {
  statement {
    sid       = "ReadThroughPredictRead"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.predict_read.arn]
  }

  statement {
    sid       = "ReadSports"
    actions   = ["dynamodb:Scan"]
    resources = [aws_dynamodb_table.sport_registry.arn]
  }

  # Each sport's live-score cache, the one its live-scores Lambda writes.
  statement {
    sid       = "ReadLiveScores"
    actions   = ["s3:GetObject"]
    resources = ["arn:aws:s3:::${local.raw_bucket_name}/*/cache/live-scores/latest.json"]
  }
}

resource "aws_iam_role_policy" "assistant_public_tools" {
  name   = "${var.project}-lambda-assistant-public-tools"
  role   = aws_iam_role.assistant_public_tools.id
  policy = data.aws_iam_policy_document.assistant_public_tools.json
}

resource "aws_iam_role" "assistant_admin_tools" {
  name               = "${var.project}-lambda-assistant-admin-tools-exec"
  assume_role_policy = data.aws_iam_policy_document.assistant_tools_assume.json

  tags = local.assistant_tags
}

resource "aws_iam_role_policy_attachment" "assistant_admin_tools_logs" {
  role       = aws_iam_role.assistant_admin_tools.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Reads the producers' aggregates, CloudWatch metrics, the events table's
# status index (pipeline health), the assistant table (usage rows) and the
# sport registry -- no Cost Explorer and no log read. GetMetricData and
# ListFunctions can't be scoped to a resource.
data "aws_iam_policy_document" "assistant_admin_tools" {
  statement {
    sid       = "ListAggregates"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.model_artifacts.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["assistant/admin/*"]
    }
  }

  statement {
    sid       = "ReadAggregates"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.model_artifacts.arn}/assistant/admin/*"]
  }

  statement {
    sid       = "ReadMetrics"
    actions   = ["cloudwatch:GetMetricData", "lambda:ListFunctions"]
    resources = ["*"]
  }

  statement {
    sid       = "ReadEventStatus"
    actions   = ["dynamodb:Query"]
    resources = ["${aws_dynamodb_table.events.arn}/index/sport-status-index"]
  }

  statement {
    sid       = "ReadAssistantUsage"
    actions   = ["dynamodb:Query"]
    resources = [aws_dynamodb_table.assistant.arn]
    condition {
      test     = "ForAllValues:StringLike"
      variable = "dynamodb:LeadingKeys"
      values   = ["usage#*"]
    }
  }

  statement {
    sid       = "ReadSports"
    actions   = ["dynamodb:Scan"]
    resources = [aws_dynamodb_table.sport_registry.arn]
  }
}

resource "aws_iam_role_policy" "assistant_admin_tools" {
  name   = "${var.project}-lambda-assistant-admin-tools"
  role   = aws_iam_role.assistant_admin_tools.id
  policy = data.aws_iam_policy_document.assistant_admin_tools.json
}

# AgentCore checks the role can pull the image when a runtime is created,
# so the runtimes wait for the role policies to propagate.
resource "time_sleep" "assistant_agent_iam_propagation" {
  depends_on = [aws_iam_role_policy.assistant_agent]
  triggers = {
    for agent, document in data.aws_iam_policy_document.assistant_agent : agent => document.json
  }
  create_duration = "30s"
}

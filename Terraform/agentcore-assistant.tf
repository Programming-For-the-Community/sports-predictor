# The in-app assistant: two agents (public: predictions and model
# performance; admin: cost and traffic too) on Amazon Bedrock AgentCore
# Runtime, both running the one image built from Source/agents/assistant.
# ASSISTANT_PROFILE picks the agent; each gets its own role
# (iam-assistant-agents.tf), Cognito JWT authorizer, guardrail, memory and
# settings. The app reaches them through CloudFront at /assistant/predictions
# and /assistant/admin (cloudfront.tf).
#
# The image tag is set by agentcore_runtime_deploy.yml after each build,
# from the "-latest" tag Terraform creates the runtimes with.

locals {
  assistant_image_name = "assistant-agent"

  # Every limit the agents and tools read (Source/library/assistant/
  # settings.py). Change one here and apply; no code release needed.
  assistant_settings = {
    ASSISTANT_MODEL_ID          = "anthropic.claude-haiku-5-5"
    ASSISTANT_EFFORT            = "low"
    ASSISTANT_MAX_TOKENS        = "4096"
    ASSISTANT_MAX_TOOL_CALLS    = "6"
    ASSISTANT_MAX_MESSAGE_CHARS = "1000"
    ASSISTANT_HISTORY_TURNS     = "10"
    ASSISTANT_MAX_SESSION_TURNS = "30"
  }
  assistant_daily_limits = {
    public = "50"
    admin  = "300"
  }
  assistant_tool_settings = {
    ASSISTANT_MAX_ROWS          = "25"
    ASSISTANT_MAX_RESULT_CHARS  = "12000"
    ASSISTANT_MAX_TREND_POINTS  = "400"
    ASSISTANT_HOURLY_TREND_DAYS = "7"
    ASSISTANT_REPEAT_MIN_BLOCKS = "100"
    ASSISTANT_REPEAT_MIN_DAYS   = "3"
  }
  # Contextual grounding on the public agent's answers -- off until the eval
  # shows invented numbers getting through (see the plan's phase 6).
  assistant_public_grounding = false

  assistant_agents = {
    public = {
      runtime_name = "sports_predictor_assistant_public"
      memory_name  = "sports_predictor_assistant_public"
      guardrail    = aws_bedrock_guardrail_version.assistant_public
      grounding    = local.assistant_public_grounding
    }
    admin = {
      runtime_name = "sports_predictor_assistant_admin"
      memory_name  = "sports_predictor_assistant_admin"
      guardrail    = aws_bedrock_guardrail_version.assistant_admin
      grounding    = false
    }
  }

  cognito_discovery_url = "https://cognito-idp.${var.region}.amazonaws.com/${aws_cognito_user_pool.main.id}/.well-known/openid-configuration"
  # ID tokens carry the app client id as their audience.
  cognito_app_clients = [aws_cognito_user_pool_client.web.id, aws_cognito_user_pool_client.mobile.id]

  assistant_tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "assistant"
  })
}

# Daily question counters (TTL'd) and saved favourite teams, keyed by the
# caller's Cognito sub -- Source/library/assistant/user_store.py.
resource "aws_dynamodb_table" "assistant" {
  name         = "${var.project}-assistant"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  deletion_protection_enabled = var.environment == "production"

  tags = local.assistant_tags
}

# Short-term conversation memory: one event per turn, under the caller's
# sub (Source/library/assistant/history.py). Kept 7 days, the shortest the
# service allows. Names allow letters, digits and underscores only.
resource "awscc_bedrockagentcore_memory" "assistant" {
  for_each = local.assistant_agents

  name                  = each.value.memory_name
  description           = "Assistant (${each.key}) conversation turns, 7 days."
  event_expiry_duration = 7

  tags = local.assistant_tags
}

resource "awscc_bedrockagentcore_runtime" "assistant" {
  for_each = local.assistant_agents

  agent_runtime_name = each.value.runtime_name
  description        = "Sports predictor assistant (${each.key}) -- Claude Haiku 5.5."
  role_arn           = aws_iam_role.assistant_agent[each.key].arn

  # The role must be able to pull the image before the runtime is created.
  depends_on = [time_sleep.assistant_agent_iam_propagation]

  agent_runtime_artifact = {
    container_configuration = {
      container_uri = "${var.ecr_repo_url}:${local.assistant_image_name}-latest"
    }
  }

  network_configuration = {
    network_mode = "PUBLIC"
  }

  protocol_configuration = "HTTP"

  # Only this pool's signed ID tokens from the app's two clients get in;
  # the admin runtime also requires the admins group.
  authorizer_configuration = {
    custom_jwt_authorizer = {
      discovery_url    = local.cognito_discovery_url
      allowed_audience = local.cognito_app_clients
      custom_claims = each.key == "admin" ? [{
        inbound_token_claim_name       = "cognito:groups"
        inbound_token_claim_value_type = "STRING_ARRAY"
        authorizing_claim_match_value = {
          claim_match_operator = "CONTAINS"
          claim_match_value = {
            match_value_string = aws_cognito_user_group.admins.name
          }
        }
      }] : null
    }
  }

  # The agent reads the caller's sub (and groups) from the verified token.
  request_header_configuration = {
    request_header_allowlist = ["Authorization"]
  }

  lifecycle_configuration = {
    idle_runtime_session_timeout = 300
    max_lifetime                 = 3600
  }

  environment_variables = merge(local.assistant_settings, {
    ASSISTANT_PROFILE          = each.key
    ASSISTANT_DAILY_LIMIT      = local.assistant_daily_limits[each.key]
    ASSISTANT_TABLE_NAME       = aws_dynamodb_table.assistant.name
    SPORT_REGISTRY_TABLE_NAME  = aws_dynamodb_table.sport_registry.name
    MEMORY_ID                  = awscc_bedrockagentcore_memory.assistant[each.key].memory_id
    GUARDRAIL_ID               = each.value.guardrail.guardrail_arn
    GUARDRAIL_VERSION          = each.value.guardrail.version
    GUARDRAIL_GROUNDING        = tostring(each.value.grounding)
    PUBLIC_TOOLS_FUNCTION_NAME = aws_lambda_function.assistant_public_tools.function_name
    ADMIN_TOOLS_FUNCTION_NAME  = each.key == "admin" ? aws_lambda_function.assistant_admin_tools.function_name : ""
  })

  tags = local.assistant_tags

  lifecycle {
    # agentcore_runtime_deploy.yml points each runtime at the versioned image.
    ignore_changes = [agent_runtime_artifact]
  }
}

# The runtimes' own log groups (stdout, spans, and the per-turn transcript
# lines from Source/library/assistant/turn_log.py), created before the
# first invocation so their retention is ours: 14 days.
resource "aws_cloudwatch_log_group" "assistant_runtime" {
  for_each = local.assistant_agents

  name              = "/aws/bedrock-agentcore/runtimes/${awscc_bedrockagentcore_runtime.assistant[each.key].agent_runtime_id}-DEFAULT"
  retention_in_days = 14

  tags = local.assistant_tags
}

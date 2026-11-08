# The assistant's two Bedrock Guardrails, applied by the agents with
# ApplyGuardrail to each question and each answer (Source/library/assistant/
# guardrail.py). The public agent also refuses gambling advice, unrelated
# tasks and infrastructure questions; both mask AWS identifiers.
# Each runtime is pinned to a published version.

locals {
  assistant_content_filters = ["HATE", "INSULTS", "SEXUAL", "VIOLENCE", "MISCONDUCT"]

  assistant_masked_patterns = {
    aws-account-id    = { pattern = "\\b\\d{12}\\b", description = "12-digit AWS account id" }
    aws-arn           = { pattern = "arn:aws[a-zA-Z-]*:[^\\s\"']+", description = "AWS resource ARN" }
    cognito-sub       = { pattern = "\\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\\b", description = "Cognito user id (UUID)" }
    internal-resource = { pattern = "${var.project}-[a-z0-9-]+", description = "This project's internal resource names" }
  }

  # Each definition describes only what it blocks: a topic defined by what
  # it is not ("anything other than predictions") matches the predictions
  # questions themselves, and wording like "picks" matches "the model's
  # picks". The system prompt keeps the agent to its own subject.
  assistant_public_topics = {
    gambling-decisions = {
      definition = "Requests for advice on placing a wager: whether to bet, which bet to place, how much money to stake, building a parlay, managing a betting bankroll, or finding a sportsbook or better odds."
      examples   = ["Should I bet the Lions this week?", "How much should I put on this parlay?", "Which bet is a lock tonight?"]
    }
    unrelated-tasks = {
      definition = "Requests for creative writing, homework, coding help, general knowledge, news, weather, politics, travel, health or personal advice."
      examples   = ["Write me a poem.", "What's the weather tomorrow?", "Help me with my homework."]
    }
    infrastructure = {
      definition = "Questions about the app's servers, AWS costs, security, logs, traffic or blocked requests."
      examples   = ["How much does this app cost to run?", "Which IPs are being blocked?", "What AWS account is this?"]
    }
  }
}

resource "aws_bedrock_guardrail" "assistant_public" {
  name                      = "${var.project}-assistant-public"
  description               = "Public assistant: content and prompt-attack filters, no gambling advice or off-topic answers, AWS identifiers and IPs masked."
  blocked_input_messaging   = "I can only help with this app's predictions and how its models have performed, and I can't help with betting decisions."
  blocked_outputs_messaging = "I couldn't answer that from the available data."

  content_policy_config {
    dynamic "filters_config" {
      for_each = local.assistant_content_filters
      content {
        type            = filters_config.value
        input_strength  = "MEDIUM"
        output_strength = "MEDIUM"
      }
    }
    filters_config {
      type            = "PROMPT_ATTACK"
      input_strength  = "HIGH"
      output_strength = "NONE"
    }
  }

  topic_policy_config {
    dynamic "topics_config" {
      for_each = local.assistant_public_topics
      content {
        name       = topics_config.key
        definition = topics_config.value.definition
        examples   = topics_config.value.examples
        type       = "DENY"
      }
    }
  }

  sensitive_information_policy_config {
    dynamic "regexes_config" {
      for_each = merge(local.assistant_masked_patterns, {
        ipv4 = { pattern = "\\b(?:\\d{1,3}\\.){3}\\d{1,3}\\b", description = "IPv4 address" }
      })
      content {
        name        = regexes_config.key
        pattern     = regexes_config.value.pattern
        description = regexes_config.value.description
        action      = "ANONYMIZE"
      }
    }
  }

  word_policy_config {
    managed_word_lists_config {
      type = "PROFANITY"
    }
  }

  dynamic "contextual_grounding_policy_config" {
    for_each = local.assistant_public_grounding ? [1] : []
    content {
      filters_config {
        type      = "GROUNDING"
        threshold = 0.75
      }
      filters_config {
        type      = "RELEVANCE"
        threshold = 0.75
      }
    }
  }

  tags = local.assistant_tags
}

resource "aws_bedrock_guardrail_version" "assistant_public" {
  guardrail_arn = aws_bedrock_guardrail.assistant_public.guardrail_arn
  description   = "Published for the public assistant runtime."

  lifecycle {
    replace_triggered_by = [aws_bedrock_guardrail.assistant_public]
  }
}

# Admins see full client IPs (repeat offenders are named by IP), so only the
# AWS identifiers are masked here.
resource "aws_bedrock_guardrail" "assistant_admin" {
  name                      = "${var.project}-assistant-admin"
  description               = "Admin assistant: content and prompt-attack filters, AWS identifiers masked."
  blocked_input_messaging   = "I can't help with that one."
  blocked_outputs_messaging = "I couldn't answer that from the available data."

  content_policy_config {
    dynamic "filters_config" {
      for_each = local.assistant_content_filters
      content {
        type            = filters_config.value
        input_strength  = "MEDIUM"
        output_strength = "MEDIUM"
      }
    }
    filters_config {
      type            = "PROMPT_ATTACK"
      input_strength  = "HIGH"
      output_strength = "NONE"
    }
  }

  sensitive_information_policy_config {
    dynamic "regexes_config" {
      for_each = local.assistant_masked_patterns
      content {
        name        = regexes_config.key
        pattern     = regexes_config.value.pattern
        description = regexes_config.value.description
        action      = "ANONYMIZE"
      }
    }
  }

  tags = local.assistant_tags
}

resource "aws_bedrock_guardrail_version" "assistant_admin" {
  guardrail_arn = aws_bedrock_guardrail.assistant_admin.guardrail_arn
  description   = "Published for the admin assistant runtime."

  lifecycle {
    replace_triggered_by = [aws_bedrock_guardrail.assistant_admin]
  }
}

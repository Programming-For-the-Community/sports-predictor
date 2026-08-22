# Routes for the two NFL serving Lambdas. All resources sit under
# aws_api_gateway_rest_api.main (api-gateway.tf).
#
#   GET /nfl/events                                              -> nfl_predict_read
#   GET /nfl/models                                              -> nfl_predict_read
#   GET /nfl/season                                              -> nfl_predict_read
#   GET /nfl/predictions/events/{event_id}                       -> nfl_predict_read (cache), async-computed by nfl_predict
#   GET /nfl/predictions/events/{event_id}/players/{entity_id}   -> nfl_predict_read (cache), async-computed by nfl_predict
#
# Deployment/stage/usage plan resources also live here -- api-gateway.tf
# defers them since AWS requires at least one method to exist first.

resource "aws_api_gateway_resource" "nfl" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_rest_api.main.root_resource_id
  path_part   = "nfl"
}

resource "aws_api_gateway_resource" "nfl_events" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.nfl.id
  path_part   = "events"
}

resource "aws_api_gateway_method" "nfl_events" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.nfl_events.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "nfl_events" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.nfl_events.id
  http_method             = aws_api_gateway_method.nfl_events.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.nfl_predict_read.invoke_arn
}

resource "aws_api_gateway_resource" "nfl_models" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.nfl.id
  path_part   = "models"
}

resource "aws_api_gateway_method" "nfl_models" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.nfl_models.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "nfl_models" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.nfl_models.id
  http_method             = aws_api_gateway_method.nfl_models.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.nfl_predict_read.invoke_arn
}

resource "aws_api_gateway_resource" "nfl_season" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.nfl.id
  path_part   = "season"
}

resource "aws_api_gateway_method" "nfl_season" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.nfl_season.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "nfl_season" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.nfl_season.id
  http_method             = aws_api_gateway_method.nfl_season.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.nfl_predict_read.invoke_arn
}

resource "aws_api_gateway_resource" "nfl_predictions" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.nfl.id
  path_part   = "predictions"
}

resource "aws_api_gateway_resource" "nfl_predictions_events" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.nfl_predictions.id
  path_part   = "events"
}

resource "aws_api_gateway_resource" "nfl_predictions_event" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.nfl_predictions_events.id
  path_part   = "{event_id}"
}

resource "aws_api_gateway_resource" "nfl_predictions_event_players" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.nfl_predictions_event.id
  path_part   = "players"
}

resource "aws_api_gateway_resource" "nfl_predictions_event_player" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.nfl_predictions_event_players.id
  path_part   = "{entity_id}"
}

# --- GET /nfl/predictions/events/{event_id} ---------------------------------

resource "aws_api_gateway_method" "nfl_predict_event" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.nfl_predictions_event.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id

  request_parameters = {
    "method.request.path.event_id" = true
  }
}

resource "aws_api_gateway_integration" "nfl_predict_event" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.nfl_predictions_event.id
  http_method             = aws_api_gateway_method.nfl_predict_event.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.nfl_predict_read.invoke_arn # cache read-through
}

# --- GET /nfl/predictions/events/{event_id}/players/{entity_id} -------------

resource "aws_api_gateway_method" "nfl_predict_player" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.nfl_predictions_event_player.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id

  request_parameters = {
    "method.request.path.event_id"    = true
    "method.request.path.entity_id"   = true
    "method.request.querystring.stat" = true
  }
}

resource "aws_api_gateway_integration" "nfl_predict_player" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.nfl_predictions_event_player.id
  http_method             = aws_api_gateway_method.nfl_predict_player.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.nfl_predict_read.invoke_arn
}

# --- CORS preflight (OPTIONS) -------------------------------------------
# Mock 200 response with CORS headers for preflight OPTIONS requests.
locals {
  cors_resources = {
    events         = aws_api_gateway_resource.nfl_events.id
    models         = aws_api_gateway_resource.nfl_models.id
    season         = aws_api_gateway_resource.nfl_season.id
    predict_event  = aws_api_gateway_resource.nfl_predictions_event.id
    predict_player = aws_api_gateway_resource.nfl_predictions_event_player.id
    live_scores    = aws_api_gateway_resource.nfl_live_scores.id
  }
}

resource "aws_api_gateway_method" "cors" {
  for_each      = local.cors_resources
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = each.value
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "cors" {
  for_each    = local.cors_resources
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = each.value
  http_method = aws_api_gateway_method.cors[each.key].http_method
  type        = "MOCK"

  request_templates = {
    "application/json" = jsonencode({ statusCode = 200 })
  }
}

resource "aws_api_gateway_method_response" "cors" {
  for_each    = local.cors_resources
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = each.value
  http_method = aws_api_gateway_method.cors[each.key].http_method
  status_code = "200"

  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "cors" {
  for_each    = local.cors_resources
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = each.value
  http_method = aws_api_gateway_method.cors[each.key].http_method
  status_code = aws_api_gateway_method_response.cors[each.key].status_code

  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = "'Content-Type,Authorization'"
    "method.response.header.Access-Control-Allow-Methods" = "'GET,OPTIONS'"
    "method.response.header.Access-Control-Allow-Origin"  = "'*'"
  }
}

# --- Deployment / stage / usage plan -----------------------------------------

resource "aws_api_gateway_deployment" "main" {
  rest_api_id = aws_api_gateway_rest_api.main.id

  triggers = {
    redeployment = sha1(jsonencode([
      aws_api_gateway_resource.nfl.id,
      aws_api_gateway_resource.nfl_predictions.id,
      aws_api_gateway_resource.nfl_predictions_events.id,
      aws_api_gateway_resource.nfl_predictions_event.id,
      aws_api_gateway_resource.nfl_predictions_event_players.id,
      aws_api_gateway_resource.nfl_predictions_event_player.id,
      aws_api_gateway_method.nfl_predict_event.id,
      aws_api_gateway_integration.nfl_predict_event.id,
      aws_api_gateway_integration.nfl_predict_event.uri,
      aws_api_gateway_method.nfl_predict_player.id,
      aws_api_gateway_integration.nfl_predict_player.id,
      aws_api_gateway_integration.nfl_predict_player.uri,
      aws_api_gateway_gateway_response.missing_auth_token.id,
      aws_api_gateway_gateway_response.default_4xx.id,
      aws_api_gateway_gateway_response.default_5xx.id,
      aws_api_gateway_resource.nfl_events.id,
      aws_api_gateway_method.nfl_events.id,
      aws_api_gateway_integration.nfl_events.id,
      # .uri included since the integration's .id (a composite key) doesn't
      # change when only .uri is updated in place.
      aws_api_gateway_integration.nfl_events.uri,
      aws_api_gateway_resource.nfl_models.id,
      aws_api_gateway_method.nfl_models.id,
      aws_api_gateway_integration.nfl_models.id,
      aws_api_gateway_integration.nfl_models.uri,
      aws_api_gateway_resource.nfl_season.id,
      aws_api_gateway_method.nfl_season.id,
      aws_api_gateway_integration.nfl_season.id,
      aws_api_gateway_integration.nfl_season.uri,
      # live-scores resources declared in api-gateway-nfl-live-scores.tf
      aws_api_gateway_resource.nfl_live_scores.id,
      aws_api_gateway_method.nfl_live_scores.id,
      aws_api_gateway_integration.nfl_live_scores.id,
      aws_api_gateway_integration.nfl_live_scores.uri,
      sha1(jsonencode(values(aws_api_gateway_method.cors)[*].id)),
      sha1(jsonencode(values(aws_api_gateway_integration.cors)[*].id)),
      sha1(jsonencode(values(aws_api_gateway_integration_response.cors)[*].id)),
      # NCAAFB routes declared in api-gateway-ncaafb-predict.tf
      aws_api_gateway_resource.ncaafb.id,
      aws_api_gateway_resource.ncaafb_events.id,
      aws_api_gateway_method.ncaafb_events.id,
      aws_api_gateway_integration.ncaafb_events.id,
      aws_api_gateway_resource.ncaafb_models.id,
      aws_api_gateway_method.ncaafb_models.id,
      aws_api_gateway_integration.ncaafb_models.id,
      aws_api_gateway_resource.ncaafb_season.id,
      aws_api_gateway_method.ncaafb_season.id,
      aws_api_gateway_integration.ncaafb_season.id,
      aws_api_gateway_resource.ncaafb_predictions.id,
      aws_api_gateway_resource.ncaafb_predictions_events.id,
      aws_api_gateway_resource.ncaafb_predictions_event.id,
      aws_api_gateway_resource.ncaafb_predictions_event_players.id,
      aws_api_gateway_resource.ncaafb_predictions_event_player.id,
      aws_api_gateway_method.ncaafb_predict_event.id,
      aws_api_gateway_integration.ncaafb_predict_event.id,
      aws_api_gateway_integration.ncaafb_predict_event.uri,
      aws_api_gateway_method.ncaafb_predict_player.id,
      aws_api_gateway_integration.ncaafb_predict_player.id,
      aws_api_gateway_integration.ncaafb_predict_player.uri,
      # live-scores resources declared in api-gateway-ncaafb-live-scores.tf
      aws_api_gateway_resource.ncaafb_live_scores.id,
      aws_api_gateway_method.ncaafb_live_scores.id,
      aws_api_gateway_integration.ncaafb_live_scores.id,
      aws_api_gateway_integration.ncaafb_live_scores.uri,
      sha1(jsonencode(values(aws_api_gateway_method.ncaafb_cors)[*].id)),
      sha1(jsonencode(values(aws_api_gateway_integration.ncaafb_cors)[*].id)),
      sha1(jsonencode(values(aws_api_gateway_integration_response.ncaafb_cors)[*].id)),
      # NBA routes declared in api-gateway-nba-predict.tf
      aws_api_gateway_resource.nba.id,
      aws_api_gateway_resource.nba_events.id,
      aws_api_gateway_method.nba_events.id,
      aws_api_gateway_integration.nba_events.id,
      aws_api_gateway_resource.nba_models.id,
      aws_api_gateway_method.nba_models.id,
      aws_api_gateway_integration.nba_models.id,
      aws_api_gateway_resource.nba_season.id,
      aws_api_gateway_method.nba_season.id,
      aws_api_gateway_integration.nba_season.id,
      aws_api_gateway_resource.nba_predictions.id,
      aws_api_gateway_resource.nba_predictions_events.id,
      aws_api_gateway_resource.nba_predictions_event.id,
      aws_api_gateway_resource.nba_predictions_event_players.id,
      aws_api_gateway_resource.nba_predictions_event_player.id,
      aws_api_gateway_method.nba_predict_event.id,
      aws_api_gateway_integration.nba_predict_event.id,
      aws_api_gateway_integration.nba_predict_event.uri,
      aws_api_gateway_method.nba_predict_player.id,
      aws_api_gateway_integration.nba_predict_player.id,
      aws_api_gateway_integration.nba_predict_player.uri,
      # live-scores resources declared in api-gateway-nba-live-scores.tf
      aws_api_gateway_resource.nba_live_scores.id,
      aws_api_gateway_method.nba_live_scores.id,
      aws_api_gateway_integration.nba_live_scores.id,
      aws_api_gateway_integration.nba_live_scores.uri,
      sha1(jsonencode(values(aws_api_gateway_method.nba_cors)[*].id)),
      sha1(jsonencode(values(aws_api_gateway_integration.nba_cors)[*].id)),
      sha1(jsonencode(values(aws_api_gateway_integration_response.nba_cors)[*].id)),
      # NCAA MBB routes declared in api-gateway-ncaambb-predict.tf. No
      # live-scores resources yet -- that's step 7.
      aws_api_gateway_resource.ncaambb.id,
      aws_api_gateway_resource.ncaambb_events.id,
      aws_api_gateway_method.ncaambb_events.id,
      aws_api_gateway_integration.ncaambb_events.id,
      aws_api_gateway_resource.ncaambb_models.id,
      aws_api_gateway_method.ncaambb_models.id,
      aws_api_gateway_integration.ncaambb_models.id,
      aws_api_gateway_resource.ncaambb_season.id,
      aws_api_gateway_method.ncaambb_season.id,
      aws_api_gateway_integration.ncaambb_season.id,
      aws_api_gateway_resource.ncaambb_predictions.id,
      aws_api_gateway_resource.ncaambb_predictions_events.id,
      aws_api_gateway_resource.ncaambb_predictions_event.id,
      aws_api_gateway_resource.ncaambb_predictions_event_players.id,
      aws_api_gateway_resource.ncaambb_predictions_event_player.id,
      aws_api_gateway_method.ncaambb_predict_event.id,
      aws_api_gateway_integration.ncaambb_predict_event.id,
      aws_api_gateway_integration.ncaambb_predict_event.uri,
      aws_api_gateway_method.ncaambb_predict_player.id,
      aws_api_gateway_integration.ncaambb_predict_player.id,
      aws_api_gateway_integration.ncaambb_predict_player.uri,
      sha1(jsonencode(values(aws_api_gateway_method.ncaambb_cors)[*].id)),
      sha1(jsonencode(values(aws_api_gateway_integration.ncaambb_cors)[*].id)),
      sha1(jsonencode(values(aws_api_gateway_integration_response.ncaambb_cors)[*].id)),
    ]))
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_api_gateway_stage" "main" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  deployment_id = aws_api_gateway_deployment.main.id
  stage_name    = var.environment
  # aws_api_gateway_documentation_version (api-gateway-documentation.tf)
  # publishes a snapshot at the API level, but the console's own
  # Documentation view and stage-scoped "Export" are both scoped to
  # whatever version the deployed STAGE itself references -- without
  # this, that snapshot exists (confirmed live: 39 documentation parts +
  # the 1.0.0 version both present via the API) but shows as empty from
  # the stage's own perspective, since nothing ever pointed this stage at
  # it.
  documentation_version = aws_api_gateway_documentation_version.main.version

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "serving"
  })
}

# Throttling only -- Cognito already authenticates every caller, no API key.
# 100 burst / 60 rps absorbs a full slate of near-simultaneous per-event
# prediction requests fanned out by event_list_page.dart.
resource "aws_api_gateway_method_settings" "main" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  stage_name  = aws_api_gateway_stage.main.stage_name
  method_path = "*/*"

  settings {
    throttling_burst_limit = 100
    throttling_rate_limit  = 60
  }
}

resource "aws_api_gateway_usage_plan" "main" {
  name = "${var.project}-usage-plan"

  api_stages {
    api_id = aws_api_gateway_rest_api.main.id
    stage  = aws_api_gateway_stage.main.stage_name
  }

  throttle_settings {
    burst_limit = 100
    rate_limit  = 60
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "serving"
  })
}

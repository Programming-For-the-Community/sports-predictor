# API Gateway's own "Documentation" feature (console: API Gateway > this
# API > Documentation) -- one aws_api_gateway_documentation_part per real
# GET endpoint (CORS OPTIONS preflight methods are intentionally excluded,
# same reason a Swagger doc normally wouldn't cover them: they're plumbing,
# not API surface). METHOD-level parts carry a summary/description;
# PATH_PARAMETER/QUERY_PARAMETER parts additionally document the two
# endpoints that take real inputs. aws_api_gateway_documentation_version
# publishes a snapshot so "Export" in the console produces a merged
# Swagger/OpenAPI file with all of this baked in, not just the bare route
# shapes.
#
# Every sport shares the same five/six-endpoint shape (events/models/
# season/predictions.../live-scores) -- one locals map keyed by a short
# id covers all of them instead of 23 near-duplicate resource blocks.
locals {
  api_documentation_methods = {
    nfl_events = {
      path        = "/nfl/events", method = "GET"
      summary     = "List NFL events"
      description = "Returns NFL events grouped by day, each with its predicted winner/margin/score (and player-prop predicted-vs-actual once completed). Supports an optional status query filter."
    }
    nfl_models = {
      path        = "/nfl/models", method = "GET"
      summary     = "List NFL model cards"
      description = "Returns the currently-promoted model card (algorithm, holdout metrics, full candidate tournament) for every NFL prediction target: win-probability, the three score-margin/home-score/away-score models, and each trained player-prop stat."
    }
    nfl_season = {
      path        = "/nfl/season", method = "GET"
      summary     = "Season projection and playoff bracket"
      description = "Returns the cached NFL season-long projection: standings/playoff/division-winner probabilities plus the reseeding-aware playoff bracket, real-vs-projected reconciled as postseason games complete. Read-through from S3, populated by a scheduled simulation, not computed per-request."
    }
    nfl_predict_event = {
      path        = "/nfl/predictions/events/{event_id}", method = "GET"
      summary     = "Single event's prediction"
      description = "Win probability, predicted score, and predicted leader-category (passing/rushing/receiving) stat lines for one NFL event, with the real result attached once the game completes. Cache read-through with async populate-on-miss (202 while a first-time prediction is still computing)."
    }
    nfl_predict_player = {
      path        = "/nfl/predictions/events/{event_id}/players/{entity_id}", method = "GET"
      summary     = "Single player-prop prediction"
      description = "Predicted value for one player/stat combination in one NFL event (e.g. a QB's passing_yards), with the real recorded stat attached once the game completes."
    }
    nfl_live_scores = {
      path        = "/nfl/live-scores", method = "GET"
      summary     = "Live in-progress scores"
      description = "Cache-only current score/clock/leader live-stat snapshot for NFL games near or at kickoff. Never triggers a DynamoDB write; populated by a short-interval EventBridge poll gated to games actually in or near their window."
    }

    ncaafb_events = {
      path        = "/ncaafb/events", method = "GET"
      summary     = "List NCAA FB events"
      description = "Returns NCAA FB (FBS) events grouped by conference then date, each with its predicted winner/margin/score and player-prop predicted-vs-actual once completed."
    }
    ncaafb_models = {
      path        = "/ncaafb/models", method = "GET"
      summary     = "List NCAA FB model cards"
      description = "Currently-promoted model card for every NCAA FB prediction target, including the national-ranking model CFP field selection is seeded from."
    }
    ncaafb_season = {
      path        = "/ncaafb/season", method = "GET"
      summary     = "Season projection and CFP bracket"
      description = "Cached NCAA FB season-long projection: conference-standings probabilities plus the 12-team CFP bracket (5 auto-bids + 7 at-large, seeded by the trained ranking model), real-vs-projected reconciled as the postseason completes."
    }
    ncaafb_predict_event = {
      path        = "/ncaafb/predictions/events/{event_id}", method = "GET"
      summary     = "Single event's prediction"
      description = "Win probability, predicted score, and predicted leader-category stat lines for one NCAA FB event, with the real result attached once the game completes."
    }
    ncaafb_predict_player = {
      path        = "/ncaafb/predictions/events/{event_id}/players/{entity_id}", method = "GET"
      summary     = "Single player-prop prediction"
      description = "Predicted value for one player/stat combination in one NCAA FB event, with the real recorded stat attached once the game completes."
    }
    ncaafb_live_scores = {
      path        = "/ncaafb/live-scores", method = "GET"
      summary     = "Live in-progress scores"
      description = "Cache-only current score/clock live snapshot for NCAA FB games near or at kickoff, matched to ESPN's own event_id. Never triggers a DynamoDB write."
    }

    nba_events = {
      path        = "/nba/events", method = "GET"
      summary     = "List NBA events"
      description = "Returns NBA events grouped by day, each with predicted winner/margin/score and top-5 predicted leaders across scoring/rebounding/assists, plus best-of-7 series-record awareness for playoff games."
    }
    nba_models = {
      path        = "/nba/models", method = "GET"
      summary     = "List NBA model cards"
      description = "Currently-promoted model card for every NBA prediction target: win-probability, the three score models, and each trained player-prop stat."
    }
    nba_season = {
      path        = "/nba/season", method = "GET"
      summary     = "Season projection, playoff bracket, and NBA Cup bracket"
      description = "Cached NBA season-long projection: standings/play-in/playoff probabilities, the play-in-aware playoff bracket, and the in-season NBA Cup knockout bracket, real-vs-projected reconciled as games complete."
    }
    nba_predict_event = {
      path        = "/nba/predictions/events/{event_id}", method = "GET"
      summary     = "Single event's prediction"
      description = "Win probability, predicted score, and predicted leader-category stat lines for one NBA event, with the real result (and, in a playoff series, the live series record) attached once the game completes."
    }
    nba_predict_player = {
      path        = "/nba/predictions/events/{event_id}/players/{entity_id}", method = "GET"
      summary     = "Single player-prop prediction"
      description = "Predicted value for one player/stat combination in one NBA event (points, rebounds, assists, steals, blocks, or three-pointers made), with the real recorded stat attached once the game completes."
    }
    nba_live_scores = {
      path        = "/nba/live-scores", method = "GET"
      summary     = "Live in-progress scores"
      description = "Cache-only current score/clock/leader live-stat snapshot for NBA games near or at tip-off. Never triggers a DynamoDB write."
    }

    ncaambb_events = {
      path        = "/ncaambb/events", method = "GET"
      summary     = "List NCAA MBB events"
      description = "Returns NCAA Division I men's basketball events grouped by day, each with predicted winner/margin/score and predicted leaders across scoring/rebounding/assists, plus a conference-game flag."
    }
    ncaambb_models = {
      path        = "/ncaambb/models", method = "GET"
      summary     = "List NCAA MBB model cards"
      description = "Currently-promoted model card for every NCAA MBB prediction target: win-probability, the three score models, the national-ranking model, and each trained player-prop stat."
    }
    ncaambb_season = {
      path        = "/ncaambb/season", method = "GET"
      summary     = "Season projection, March Madness bracket, and conference brackets"
      description = "Cached NCAA MBB season-long projection. Currently always returns 503 (not yet available) -- season_projection.py's scheduled write, the conference-tournament brackets, and the March Madness bracket are a later build step; the route and its read-through path already exist so no further API Gateway wiring is needed once that step ships."
    }
    ncaambb_predict_event = {
      path        = "/ncaambb/predictions/events/{event_id}", method = "GET"
      summary     = "Single event's prediction"
      description = "Win probability, predicted score, and predicted leader-category stat lines for one NCAA MBB event, with the real result attached once the game completes."
    }
    ncaambb_predict_player = {
      path        = "/ncaambb/predictions/events/{event_id}/players/{entity_id}", method = "GET"
      summary     = "Single player-prop prediction"
      description = "Predicted value for one player/stat combination in one NCAA MBB event (points, rebounds, assists, steals, blocks, or three-pointers made), with the real recorded stat attached once the game completes."
    }
    ncaambb_live_scores = {
      path        = "/ncaambb/live-scores", method = "GET"
      summary     = "Live in-progress scores"
      description = "Cache-only current score/clock/leader live-stat snapshot for NCAA MBB games near or at tip-off. Never triggers a DynamoDB write."
    }
  }

  # The two endpoints that take real inputs, documented per-parameter --
  # every sport's shape is identical here, so this loops sport x param
  # rather than repeating 4 near-identical blocks by hand.
  api_documentation_predict_event_params = {
    for sport in ["nfl", "ncaafb", "nba", "ncaambb"] : "${sport}_event_id" => {
      path = "/${sport}/predictions/events/{event_id}"
      name = "event_id"
    }
  }

  api_documentation_predict_player_params = merge(
    { for sport in ["nfl", "ncaafb", "nba", "ncaambb"] : "${sport}_player_event_id" => {
      path = "/${sport}/predictions/events/{event_id}/players/{entity_id}"
      name = "event_id"
    } },
    { for sport in ["nfl", "ncaafb", "nba", "ncaambb"] : "${sport}_player_entity_id" => {
      path = "/${sport}/predictions/events/{event_id}/players/{entity_id}"
      name = "entity_id"
    } },
  )
}

resource "aws_api_gateway_documentation_part" "methods" {
  for_each    = local.api_documentation_methods
  rest_api_id = aws_api_gateway_rest_api.main.id

  location {
    type   = "METHOD"
    path   = each.value.path
    method = each.value.method
  }

  properties = jsonencode({
    summary     = each.value.summary
    description = each.value.description
  })
}

resource "aws_api_gateway_documentation_part" "predict_event_path_params" {
  for_each    = local.api_documentation_predict_event_params
  rest_api_id = aws_api_gateway_rest_api.main.id

  location {
    type   = "PATH_PARAMETER"
    path   = each.value.path
    method = "GET"
    name   = each.value.name
  }

  properties = jsonencode({
    description = "ESPN's own numeric event id for this game."
  })
}

resource "aws_api_gateway_documentation_part" "predict_player_path_params" {
  for_each    = local.api_documentation_predict_player_params
  rest_api_id = aws_api_gateway_rest_api.main.id

  location {
    type   = "PATH_PARAMETER"
    path   = each.value.path
    method = "GET"
    name   = each.value.name
  }

  properties = jsonencode({
    description = each.value.name == "event_id" ? "ESPN's own numeric event id for this game." : "ESPN's own numeric athlete id for this player."
  })
}

# stat is optional on the player-prop endpoint (omit it to get every
# trained stat for this player/event; pass it to narrow to one).
resource "aws_api_gateway_documentation_part" "predict_player_stat_query_param" {
  for_each    = toset(["nfl", "ncaafb", "nba", "ncaambb"])
  rest_api_id = aws_api_gateway_rest_api.main.id

  location {
    type   = "QUERY_PARAMETER"
    path   = "/${each.value}/predictions/events/{event_id}/players/{entity_id}"
    method = "GET"
    name   = "stat"
  }

  properties = jsonencode({
    description = "Optional. Narrows the response to one trained player-prop stat instead of every stat trained for this sport."
  })
}

# The console's Documentation pane has its own "Authorizers" tab, separate
# from "Resources and methods" above -- it was empty not because of a bug,
# but because no AUTHORIZER-type documentation_part existed at all for the
# one authorizer this API has (api-gateway.tf's Cognito authorizer).
resource "aws_api_gateway_documentation_part" "cognito_authorizer" {
  rest_api_id = aws_api_gateway_rest_api.main.id

  location {
    type = "AUTHORIZER"
    name = aws_api_gateway_authorizer.cognito.name
  }

  properties = jsonencode({
    description = "Validates the caller's Cognito User Pool JWT (Authorization header). Required on every route in this API except the CORS OPTIONS preflight methods."
  })
}

resource "aws_api_gateway_documentation_version" "main" {
  # No automatic bump trigger -- this is a label, not a hash of the
  # content above. Bump it by hand whenever the console's exported
  # Swagger/OpenAPI snapshot needs to reflect a real documentation content
  # change, same as every other "explicit version bump, not implicit"
  # convention this project uses. 1.1.0: added the AUTHORIZER part above,
  # which a bare 1.0.0 (already a cut, immutable snapshot) can't retroactively
  # pick up -- the stage's own documentation_version has to move too, or
  # Export keeps serving the pre-authorizer snapshot forever.
  version     = "1.1.0"
  rest_api_id = aws_api_gateway_rest_api.main.id

  depends_on = [
    aws_api_gateway_documentation_part.methods,
    aws_api_gateway_documentation_part.predict_event_path_params,
    aws_api_gateway_documentation_part.predict_player_path_params,
    aws_api_gateway_documentation_part.predict_player_stat_query_param,
    aws_api_gateway_documentation_part.cognito_authorizer,
  ]
}

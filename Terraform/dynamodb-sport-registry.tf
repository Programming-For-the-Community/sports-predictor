# Drives the ingest-orchestrator and training-orchestrator Step Functions
# Map states (sfn-ingest-orchestrator.tf, sfn-training-orchestrator.tf) --
# one row per sport: polling cadence, an active flag (season on/off
# switch), and a training_targets list. See design/DATA_SCHEMA.md's Sport
# registry table section.
resource "aws_dynamodb_table" "sport_registry" {
  name         = local.sport_registry_table
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "sport_key"

  attribute {
    name = "sport_key"
    type = "S"
  }

  deletion_protection_enabled = var.environment == "production"

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "storage"
  })
}

# The full set of NFL training targets. Adding one is a one-line change
# to the relevant map below.
locals {
  nfl_score_targets = {
    "margin"     = true
    "home_score" = true
    "away_score" = true
  }
  nfl_player_prop_stats = {
    "passing_yards"        = true
    "passing_touchdowns"   = true
    "rushing_yards"        = true
    "rushing_touchdowns"   = true
    "receiving_yards"      = true
    "receiving_touchdowns" = true
    "defensive_sacks"      = true
  }
}

# NFL's own registry row, managed as data (not applied by hand) -- see
# design/DATA_SCHEMA.md for the full attribute reference. `active` is the
# season on/off switch; the orchestrators' own schedules run year-round
# (sfn-ingest-orchestrator.tf), so this is what gates a sport in or out.
#
# training_targets: task_definition_suffix appends to
# "${var.project}-<sport>-" to resolve the ECS task-definition family at
# runtime (sfn-training-orchestrator.tf); container_name must match that
# task definition's own container name. env_name/env_value is a flat pair
# rather than a list because every training script here takes at most one
# override (SCORE_TARGET or TARGET_STAT), directly readable via JSONPath
# in the state machine's ContainerOverrides. win-probability has no real
# override, so it re-asserts AWS_REGION at its own value as a no-op.
resource "aws_dynamodb_table_item" "nfl_registry" {
  table_name = aws_dynamodb_table.sport_registry.name
  hash_key   = aws_dynamodb_table.sport_registry.hash_key

  item = jsonencode({
    sport_key       = { S = "SPORT#NFL" }
    sport           = { S = "nfl" }
    event_type      = { S = "head_to_head" }
    polling_cadence = { S = "daily" }
    active          = { BOOL = true }

    training_targets = {
      L = concat(
        [
          {
            M = {
              model_name             = { S = "win-probability" }
              task_definition_suffix = { S = "train-win-probability-model" }
              container_name         = { S = "nfl-train-win-probability-model" }
              env_name               = { S = "AWS_REGION" }
              env_value              = { S = var.region }
            }
          },
        ],
        [
          for target, _ in local.nfl_score_targets : {
            M = {
              model_name             = { S = "score-${replace(target, "_", "-")}" }
              task_definition_suffix = { S = "train-score-model" }
              container_name         = { S = "nfl-train-score-model" }
              env_name               = { S = "SCORE_TARGET" }
              env_value              = { S = target }
            }
          }
        ],
        [
          for stat, _ in local.nfl_player_prop_stats : {
            M = {
              model_name             = { S = "player-prop-${replace(stat, "_", "-")}" }
              task_definition_suffix = { S = "train-player-prop-model" }
              container_name         = { S = "nfl-train-player-prop-model" }
              env_name               = { S = "TARGET_STAT" }
              env_value              = { S = stat }
            }
          }
        ],
      )
    }
  })

  # current_model_version is deliberately absent from the item above --
  # that pointer is meant to be moved by the (future, Phase 7)
  # model-promotion approval flow, not by a `terraform apply`.
}

# Same 7 player-prop stats as NFL (TARGET_STAT values verified against
# CFBD's actual field names -- see project memory's Phase 1 notes).
locals {
  ncaafb_score_targets = {
    "margin"     = true
    "home_score" = true
    "away_score" = true
  }
  ncaafb_player_prop_stats = {
    "passing_yards"        = true
    "passing_touchdowns"   = true
    "rushing_yards"        = true
    "rushing_touchdowns"   = true
    "receiving_yards"      = true
    "receiving_touchdowns" = true
    "defensive_sacks"      = true
  }
}

# NCAAFB's registry row -- same shape as nfl_registry above, plus one extra
# training target with no real env-var override: national-ranking is a
# team-week model, not scoped by a SCORE_TARGET/TARGET_STAT the way
# score/player-prop targets are, so it re-asserts AWS_REGION as a no-op,
# same as win-probability does.
resource "aws_dynamodb_table_item" "ncaafb_registry" {
  table_name = aws_dynamodb_table.sport_registry.name
  hash_key   = aws_dynamodb_table.sport_registry.hash_key

  item = jsonencode({
    sport_key       = { S = "SPORT#NCAAFB" }
    sport           = { S = "ncaafb" }
    event_type      = { S = "head_to_head" }
    polling_cadence = { S = "daily" }
    active          = { BOOL = true }

    training_targets = {
      L = concat(
        [
          {
            M = {
              model_name             = { S = "win-probability" }
              task_definition_suffix = { S = "train-win-probability-model" }
              container_name         = { S = "ncaafb-train-win-probability-model" }
              env_name               = { S = "AWS_REGION" }
              env_value              = { S = var.region }
            }
          },
          {
            M = {
              model_name             = { S = "national-ranking" }
              task_definition_suffix = { S = "train-ranking-model" }
              container_name         = { S = "ncaafb-train-ranking-model" }
              env_name               = { S = "AWS_REGION" }
              env_value              = { S = var.region }
            }
          },
        ],
        [
          for target, _ in local.ncaafb_score_targets : {
            M = {
              model_name             = { S = "score-${replace(target, "_", "-")}" }
              task_definition_suffix = { S = "train-score-model" }
              container_name         = { S = "ncaafb-train-score-model" }
              env_name               = { S = "SCORE_TARGET" }
              env_value              = { S = target }
            }
          }
        ],
        [
          for stat, _ in local.ncaafb_player_prop_stats : {
            M = {
              model_name             = { S = "player-prop-${replace(stat, "_", "-")}" }
              task_definition_suffix = { S = "train-player-prop-model" }
              container_name         = { S = "ncaafb-train-player-prop-model" }
              env_name               = { S = "TARGET_STAT" }
              env_value              = { S = stat }
            }
          }
        ],
      )
    }
  })
}

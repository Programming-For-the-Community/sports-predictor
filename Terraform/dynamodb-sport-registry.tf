# Drives the ingest-orchestrator and training-orchestrator Step Functions
# Map states (sfn-ingest-orchestrator.tf, sfn-training-orchestrator.tf) --
# one row per sport, storing polling cadence, current model version, an
# active flag (season on/off switch), and its training_targets list (the
# runtime replacement for what used to be Terraform for_each maps in
# scheduler-nfl-train-score-model.tf/scheduler-nfl-train-player-prop-model.tf).
#
# See design/DATA_SCHEMA.md (Sport registry table section) and
# design/PROJECT_PLAN.md Phase 4 for the full context.
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

# The full set of NFL training targets -- moved here (from what used to be
# separate locals blocks in scheduler-nfl-train-score-model.tf and
# scheduler-nfl-train-player-prop-model.tf, both deleted by the Step
# Functions migration) since the sport registry item below is now their
# one source of truth. Adding a target is a one-line change to one of
# these maps, same as before.
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
# season on/off switch: the orchestrators' own schedules run year-round
# (see sfn-ingest-orchestrator.tf), so flipping this to false after the
# season (and back before the next one) is what used to be encoded as
# scheduler-nfl-ingest.tf's Aug-Feb cron window.
#
# training_targets mirrors the win-probability/score/player-prop targets
# above -- task_definition_suffix is appended to "${var.project}-<sport>-"
# to resolve the ECS task-definition family at runtime (see
# sfn-training-orchestrator.tf), and container_name must match that task
# definition's own container name (the target of its ContainerOverrides).
#
# env_name/env_value (rather than a list of override pairs) is
# deliberate: every training script this project has ever needed takes at
# most ONE override (SCORE_TARGET or TARGET_STAT) -- a flat pair is
# directly readable via JSONPath in the state machine's ContainerOverrides
# (sfn-training-orchestrator.tf), where zipping a variable-length list of
# {name, value} pairs into ECS's Environment shape isn't expressible
# without a Lambda helper. win-probability has no real override, so it
# gets a harmless no-op (re-asserting AWS_REGION at its own value) rather
# than a special-cased empty-list branch in the state machine.
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

# Weekly full-season NFL schedule sync -- unlike the daily ingest_orchestrator
# (sfn-ingest-orchestrator.tf), which invokes nfl-ingest with no override and
# so only ever fetches whichever single week "most recent Sunday" auto-detects,
# this walks EVERY week of the current season so the full remaining schedule
# is always available for season_simulation.py's Monte Carlo and the
# frontend's upcoming-events list -- see Source/aws-lambdas/nfl/predict/
# handler.py's _season_standings_inputs (remaining_games) and
# library.serving.nfl_reads._next_week_events, neither of which had anything
# to look ahead into before this existed.
#
# NFL-specific for now (the week/season-type structure below is NFL's own),
# not folded into the generic per-sport ingest_orchestrator -- that one fans
# out over *sports*, this fans out over *weeks of one sport*, a different
# shape that doesn't generalize cleanly yet. Same "generalize when a second
# sport actually needs it" reasoning as PLAYER_PROP_STATS's own duplication
# comment in predict/handler.py.
#
# Mirrors data-backfills/nfl/backfill.py's REGULAR_SEASON_WEEKS (1-18) /
# POSTSEASON_WEEKS (1-5) / SEASON_TYPES ranges -- same NFL structural fact,
# expressed here in Terraform since this state machine's Map needs it as
# static input, not something to duplicate by hand as 23 literal JSON lines.
# Preseason (season_type 1) deliberately absent, matching ingest/handler.py's
# own PRESEASON_TYPE skip.
locals {
  nfl_season_weeks = concat(
    [for week in range(1, 19) : { season_type = 2, week = week }],
    [for week in range(1, 6) : { season_type = 3, week = week }],
  )
}

resource "aws_sfn_state_machine" "nfl_season_schedule_sync" {
  name     = "${var.project}-nfl-season-schedule-sync"
  role_arn = aws_iam_role.stepfunctions_orchestrator.arn
  type     = "STANDARD"

  definition = <<EOF
{
  "Comment": "Walks every week of the current NFL season, invoking nfl-ingest for each with season omitted (resolved from the calendar -- see ingest/handler.py's _current_nfl_season) so future/off-season weeks get seeded, not just whichever week auto-detect would pick.",
  "StartAt": "SeasonWeeks",
  "States": {
    "SeasonWeeks": {
      "Type": "Pass",
      "Result": {
        "weeks": ${jsonencode(local.nfl_season_weeks)}
      },
      "Next": "ForEachWeek"
    },
    "ForEachWeek": {
      "Type": "Map",
      "ItemsPath": "$.weeks",
      "MaxConcurrency": 3,
      "ItemProcessor": {
        "ProcessorConfig": {
          "Mode": "INLINE"
        },
        "StartAt": "SyncWeek",
        "States": {
          "SyncWeek": {
            "Type": "Task",
            "Resource": "arn:aws:states:::lambda:invoke",
            "Parameters": {
              "FunctionName": "${aws_lambda_function.nfl_ingest.function_name}",
              "Payload.$": "$"
            },
            "Catch": [
              {
                "ErrorEquals": ["States.ALL"],
                "Next": "WeekSyncFailed"
              }
            ],
            "End": true
          },
          "WeekSyncFailed": {
            "Type": "Pass",
            "Comment": "One week's sync failing (a transient ESPN error, most likely) doesn't block the rest of the season's weeks from syncing -- next Thursday's run retries it anyway.",
            "End": true
          }
        }
      },
      "End": true
    }
  }
}
EOF

  tags = merge(local.common_tags, {
    Sport     = "nfl"
    Component = "ingestion"
  })
}

# Replaces scheduler-nfl-ingest.tf's direct EventBridge -> Lambda wiring
# with a registry-driven fan-out: scans the sport registry for active
# sports and invokes each one's own ingest Lambda by name, resolved at
# runtime via the "${var.project}-<sport>-ingest" naming convention every
# ingest Lambda already follows (see lambda-nfl-ingest.tf). Onboarding a
# new sport means deploying its own ingest Lambda under that name and
# adding a registry row -- no state machine change.
#
# Triggered by scheduler-ingest-orchestrator.tf, once daily, year-round --
# season-gating that used to be scheduler-nfl-ingest.tf's Aug-Feb cron
# window is now each sport's own `active` flag on its registry row (see
# dynamodb-sport-registry.tf).
#
# Standard, not Express: this project's volume (single-digit sports,
# daily cadence) is a few hundred state transitions/month at most, well
# inside Step Functions' always-free 4,000/month tier -- see this
# session's cost comparison in the design/PROJECT_PLAN.md Phase 4 update.
# Standard's 90-day execution history is also a real debugging aid for a
# low-frequency personal pipeline that Express doesn't provide.
resource "aws_sfn_state_machine" "ingest_orchestrator" {
  name     = "${var.project}-ingest-orchestrator"
  role_arn = aws_iam_role.stepfunctions_orchestrator.arn
  type     = "STANDARD"

  definition = <<EOF
{
  "Comment": "Scans the sport registry for active sports and invokes each one's ingest Lambda.",
  "StartAt": "ScanActiveSports",
  "States": {
    "ScanActiveSports": {
      "Type": "Task",
      "Resource": "arn:aws:states:::aws-sdk:dynamodb:scan",
      "Parameters": {
        "TableName": "${aws_dynamodb_table.sport_registry.name}",
        "FilterExpression": "active = :active",
        "ExpressionAttributeValues": {
          ":active": {"BOOL": true}
        }
      },
      "ResultSelector": {
        "sports.$": "$.Items[*].sport.S"
      },
      "Next": "ForEachActiveSport"
    },
    "ForEachActiveSport": {
      "Type": "Map",
      "ItemsPath": "$.sports",
      "MaxConcurrency": 5,
      "ItemProcessor": {
        "ProcessorConfig": {
          "Mode": "INLINE"
        },
        "StartAt": "InvokeIngestLambda",
        "States": {
          "InvokeIngestLambda": {
            "Type": "Task",
            "Resource": "arn:aws:states:::lambda:invoke",
            "Parameters": {
              "FunctionName.$": "States.Format('${var.project}-{}-ingest', $)"
            },
            "Catch": [
              {
                "ErrorEquals": ["States.ALL"],
                "Next": "IngestFailed"
              }
            ],
            "End": true
          },
          "IngestFailed": {
            "Type": "Pass",
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
    Sport     = "shared"
    Component = "orchestration"
  })
}

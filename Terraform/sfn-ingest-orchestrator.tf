# Registry-driven fan-out: scans the sport registry for active sports and
# invokes each one's own ingest Lambda by name, resolved at runtime via
# the "${var.project}-<sport>-ingest" naming convention every ingest
# Lambda follows (see lambda-nfl-ingest.tf). Onboarding a new sport means
# deploying its own ingest Lambda under that name and adding a registry
# row -- no state machine change. Triggered by
# scheduler-ingest-orchestrator.tf, once daily, year-round -- season
# gating is each sport's own `active` flag on its registry row (see
# dynamodb-sport-registry.tf).
#
# Standard, not Express: this project's volume (single-digit sports,
# daily cadence) is a few hundred state transitions/month, well inside
# Step Functions' always-free 4,000/month tier, and Standard's 90-day
# execution history is a real debugging aid for a low-frequency pipeline.
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
        "TableName": "${aws_dynamodb_table.sport_registry.name}"
      },
      "ResultSelector": {
        "items.$": "$.Items"
      },
      "Next": "ForEachSport"
    },
    "ForEachSport": {
      "Type": "Map",
      "ItemsPath": "$.items",
      "MaxConcurrency": 5,
      "ItemProcessor": {
        "ProcessorConfig": {
          "Mode": "INLINE"
        },
        "StartAt": "IsActive",
        "States": {
          "IsActive": {
            "Type": "Choice",
            "Comment": "Filtered here, not in the Scan above -- aws-sdk:dynamodb:scan rejects a BOOL-typed FilterExpression value. Variable is $.active.Bool: the aws-sdk: integration marshals DynamoDB's Boolean type as \"Bool\", not \"BOOL\".",
            "Choices": [
              {
                "Variable": "$.active.Bool",
                "BooleanEquals": true,
                "Next": "InvokeIngestLambda"
              }
            ],
            "Default": "SportInactive"
          },
          "SportInactive": {
            "Type": "Pass",
            "End": true
          },
          "InvokeIngestLambda": {
            "Type": "Task",
            "Resource": "arn:aws:states:::lambda:invoke",
            "Parameters": {
              "FunctionName.$": "States.Format('${var.project}-{}-ingest', $.sport.S)"
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

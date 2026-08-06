# Replaces scheduler-nfl-feature-engineering.tf + scheduler-nfl-train-
# win-probability-model.tf + the deleted scheduler-nfl-train-score-model.tf/
# scheduler-nfl-train-player-prop-model.tf (4 files, 11 EventBridge
# Scheduler resources for NFL alone) with one registry-driven state
# machine: for each active sport, run its feature-engineering task, then
# fan out over its own training_targets list (see dynamodb-sport-
# registry.tf) to run every training target as its own ECS task.
#
# Feature-engineering is now a real upstream dependency of training
# within one execution, not just an earlier cron slot with an assumed
# margin (the old design's 11:00/12:00 UTC gap just hoped feature
# engineering finished within the hour). The inner Map's MaxConcurrency
# replaces the old design's hand-picked 30-minute stagger between
# training schedules, which existed purely to stay under the account's
# Fargate on-demand vCPU quota -- a declarative concurrency cap does the
# same job without needing a new time slot picked by hand for every
# additional target.
#
# Task definitions/images themselves are untouched by this migration --
# see ecs-task-nfl-feature-engineering.tf and the three
# ecs-task-nfl-train-*.tf files, none of which changed. Only what
# triggers them did.
resource "aws_sfn_state_machine" "training_orchestrator" {
  name     = "${var.project}-training-orchestrator"
  role_arn = aws_iam_role.stepfunctions_orchestrator.arn
  type     = "STANDARD"

  definition = <<EOF
{
  "Comment": "Scans the sport registry for active sports, rebuilds each one's training dataset, then trains every one of its registered targets.",
  "StartAt": "ScanActiveSports",
  "States": {
    "ScanActiveSports": {
      "Type": "Task",
      "Resource": "arn:aws:states:::aws-sdk:dynamodb:scan",
      "Parameters": {
        "TableName": "${aws_dynamodb_table.sport_registry.name}"
      },
      "Next": "ForEachSport"
    },
    "ForEachSport": {
      "Type": "Map",
      "ItemsPath": "$.Items",
      "MaxConcurrency": 3,
      "ItemProcessor": {
        "ProcessorConfig": {
          "Mode": "INLINE"
        },
        "StartAt": "IsActive",
        "States": {
          "IsActive": {
            "Type": "Choice",
            "Comment": "Filtered here, not in the Scan above -- Step Functions' aws-sdk:dynamodb:scan integration schema rejects a BOOL-typed ExpressionAttributeValue in a FilterExpression. Variable is $.active.Bool, not $.active.BOOL -- confirmed live via a real execution's Choice-state input that the aws-sdk: integration marshals DynamoDB's Boolean attribute type as \"Bool\", not the raw API's \"BOOL\" (S/M/L come through as expected) -- a JSONPath mismatch here throws States.Runtime (\"Invalid path\"), it does not fall through to Default.",
            "Choices": [
              {
                "Variable": "$.active.Bool",
                "BooleanEquals": true,
                "Next": "RunFeatureEngineering"
              }
            ],
            "Default": "SportInactive"
          },
          "SportInactive": {
            "Type": "Pass",
            "End": true
          },
          "RunFeatureEngineering": {
            "Type": "Task",
            "Resource": "arn:aws:states:::ecs:runTask.sync",
            "Parameters": {
              "Cluster": "${aws_ecs_cluster.main.arn}",
              "TaskDefinition.$": "States.Format('${var.project}-{}-feature-engineering', $.sport.S)",
              "LaunchType": "FARGATE",
              "NetworkConfiguration": {
                "AwsvpcConfiguration": {
                  "Subnets": ["${aws_subnet.public_1.id}", "${aws_subnet.public_2.id}", "${aws_subnet.public_3.id}"],
                  "SecurityGroups": ["${aws_security_group.fargate_internet_egress.id}"],
                  "AssignPublicIp": "ENABLED"
                }
              }
            },
            "ResultPath": "$.feature_engineering_result",
            "Catch": [
              {
                "ErrorEquals": ["States.ALL"],
                "Next": "FeatureEngineeringFailed"
              }
            ],
            "Next": "TrainAllTargets"
          },
          "FeatureEngineeringFailed": {
            "Type": "Pass",
            "Comment": "Skip training entirely for this sport this run -- training against a dataset feature engineering failed to rebuild would train on stale or partial data.",
            "End": true
          },
          "TrainAllTargets": {
            "Type": "Map",
            "ItemsPath": "$.training_targets.L",
            "MaxConcurrency": 3,
            "ItemSelector": {
              "sport.$": "$.sport.S",
              "target.$": "$$.Map.Item.Value"
            },
            "ItemProcessor": {
              "ProcessorConfig": {
                "Mode": "INLINE"
              },
              "StartAt": "RunTrainingTask",
              "States": {
                "RunTrainingTask": {
                  "Type": "Task",
                  "Resource": "arn:aws:states:::ecs:runTask.sync",
                  "Parameters": {
                    "Cluster": "${aws_ecs_cluster.main.arn}",
                    "TaskDefinition.$": "States.Format('${var.project}-{}-{}', $.sport, $.target.M.task_definition_suffix.S)",
                    "LaunchType": "FARGATE",
                    "NetworkConfiguration": {
                      "AwsvpcConfiguration": {
                        "Subnets": ["${aws_subnet.public_1.id}", "${aws_subnet.public_2.id}", "${aws_subnet.public_3.id}"],
                        "SecurityGroups": ["${aws_security_group.fargate_internet_egress.id}"],
                        "AssignPublicIp": "ENABLED"
                      }
                    },
                    "Overrides": {
                      "ContainerOverrides": [
                        {
                          "Name.$": "$.target.M.container_name.S",
                          "Environment": [
                            {
                              "Name.$": "$.target.M.env_name.S",
                              "Value.$": "$.target.M.env_value.S"
                            }
                          ]
                        }
                      ]
                    }
                  },
                  "Catch": [
                    {
                      "ErrorEquals": ["States.ALL"],
                      "Next": "TrainingTaskFailed"
                    }
                  ],
                  "End": true
                },
                "TrainingTaskFailed": {
                  "Type": "Pass",
                  "Comment": "One target's training failure doesn't block the rest of this sport's targets -- each target is independently versioned (library/ml/training_common.py), so a failed run here just means this week's retrain didn't happen for this one target.",
                  "End": true
                }
              }
            },
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

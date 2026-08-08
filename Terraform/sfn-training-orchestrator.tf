# CloudWatch Logs destination for training_orchestrator's
# logging_configuration below -- Distributed Map's own per-iteration
# status (dispatched/succeeded/failed child executions) shows up here,
# not just in the top-level execution's own graph, which renders
# TrainAllTargets as a single node with no per-iteration detail the way
# INLINE Map's iteration array used to.
resource "aws_cloudwatch_log_group" "training_orchestrator" {
  name              = "/aws/vendedlogs/states/${var.project}-training-orchestrator"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "orchestration"
  })
}

# One registry-driven state machine: for each active sport, run its
# feature-engineering task, then fan out over its own training_targets
# list (see dynamodb-sport-registry.tf) to run every training target as
# its own ECS task. TrainAllTargets' MaxConcurrency (locals-training-
# compute.tf) caps how many training tasks run at once, to stay under the
# account's Fargate on-demand vCPU quota -- and runs in Distributed mode
# specifically because that cap can be well above Standard/INLINE Map's
# hard 40-concurrent-iteration ceiling once training_task_vcpu is sized
# down. ForEachSport (the outer, per-sport Map) stays INLINE -- it isn't
# vCPU-budget-constrained the way TrainAllTargets is, and today only ever
# fans out over one active sport.
resource "aws_sfn_state_machine" "training_orchestrator" {
  name     = "${var.project}-training-orchestrator"
  role_arn = aws_iam_role.stepfunctions_orchestrator.arn
  type     = "STANDARD"

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.training_orchestrator.arn}:*"
    include_execution_data = true
    level                  = "ALL"
  }

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
            "Comment": "Filtered here, not in the Scan above -- aws-sdk:dynamodb:scan rejects a BOOL-typed FilterExpression value. Variable is $.active.Bool: the aws-sdk: integration marshals DynamoDB's Boolean type as \"Bool\", not \"BOOL\".",
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
              "PropagateTags": "TASK_DEFINITION",
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
            "MaxConcurrency": ${local.training_max_concurrency},
            "ItemSelector": {
              "sport.$": "$.sport.S",
              "target.$": "$$.Map.Item.Value"
            },
            "ItemProcessor": {
              "ProcessorConfig": {
                "Mode": "DISTRIBUTED",
                "ExecutionType": "STANDARD"
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
                    "PropagateTags": "TASK_DEFINITION",
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

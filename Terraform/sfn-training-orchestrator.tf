# CloudWatch Logs destination for training_orchestrator's
# logging_configuration below. Distributed Map's own per-iteration status
# (dispatched/succeeded/failed child executions) shows up here, not just
# in the top-level execution's own graph.
resource "aws_cloudwatch_log_group" "training_orchestrator" {
  name              = "/aws/vendedlogs/states/${var.project}-training-orchestrator"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "orchestration"
  })
}

# One registry-driven state machine: for each in-season sport (per its
# season_start/season_end window, checked via the season-gate Lambda --
# lambda-season-gate.tf), run its feature-engineering task, then fan out
# over its own training_targets list (see dynamodb-sport-registry.tf) to
# run every training target as its own ECS task. TrainAllTargets'
# MaxConcurrency (locals-training-compute.tf) caps how many training
# tasks run at once, to stay under the account's Fargate on-demand vCPU
# quota, and runs in Distributed mode since that cap can exceed
# Standard/INLINE Map's hard 40-concurrent-iteration ceiling.
#
# ForEachSport (the outer, per-sport Map) stays INLINE, with its own
# MaxConcurrency pinned to 1. training_vcpu_budget_fraction
# (locals-training-compute.tf) is sized as a fraction of the whole
# account's Fargate quota, assuming only one sport's TrainAllTargets Map
# spends it at a time; serializing sports here is what enforces that
# shared limit.
resource "aws_sfn_state_machine" "training_orchestrator" {
  name     = "${var.project}-training-orchestrator"
  role_arn = aws_iam_role.stepfunctions_orchestrator.arn
  type     = "STANDARD"

  # Waits for IAM/CloudWatch policy propagation -- see
  # iam-stepfunctions-orchestrator.tf's time_sleep.iam_propagation.
  depends_on = [time_sleep.iam_propagation]

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.training_orchestrator.arn}:*"
    include_execution_data = true
    level                  = "ALL"
  }

  definition = <<EOF
{
  "Comment": "Scans the sport registry for in-season sports, rebuilds each one's training dataset, then trains every one of its registered targets.",
  "StartAt": "ScanSportRegistry",
  "States": {
    "ScanSportRegistry": {
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
      "MaxConcurrency": 1,
      "Comment": "Pinned to 1, not left at a higher default -- see this file's top comment. training_vcpu_budget_fraction assumes only one sport's TrainAllTargets Map spends it at a time; running sports concurrently here would let each independently spend the full budget with no cross-sport coordination. local.feature_engineering_max_concurrency (locals-feature-engineering-compute.tf) already computes the real on-demand headroom available if this is ever raised -- unused today since this stays at 1.",
      "ItemProcessor": {
        "ProcessorConfig": {
          "Mode": "INLINE"
        },
        "StartAt": "CheckSeason",
        "States": {
          "CheckSeason": {
            "Type": "Task",
            "Resource": "arn:aws:states:::lambda:invoke",
            "Comment": "season_start/season_end are year-agnostic \"MM-DD\" strings (dynamodb-sport-registry.tf) -- season-gate (lambda-season-gate.tf) recomputes membership fresh every run rather than reading a stored on/off bit.",
            "Parameters": {
              "FunctionName": "${aws_lambda_function.season_gate.function_name}",
              "Payload": {
                "season_start.$": "$.season_start.S",
                "season_end.$": "$.season_end.S"
              }
            },
            "ResultSelector": {
              "in_season.$": "$.Payload.in_season"
            },
            "ResultPath": "$.season_check",
            "Catch": [
              {
                "ErrorEquals": ["States.ALL"],
                "Next": "SportInactive"
              }
            ],
            "Next": "IsInSeason"
          },
          "IsInSeason": {
            "Type": "Choice",
            "Choices": [
              {
                "Variable": "$.season_check.in_season",
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
                  "Comment": "Fargate Spot (~68% cheaper than on-demand) -- library/ml/backtest.py's run_backtest now promotes each tournament candidate the moment it beats what's live, specifically so a Spot reclaim mid-run doesn't lose the whole training run's progress, just whatever candidate was actively fitting. TRAINING_RUN_ID (below, $$.Execution.Name -- not .Id, which is the full execution ARN and an uglier S3 key for no benefit) stays identical across a Retry attempt and the Catch-driven RunTrainingTaskOnDemand fallback, since both stay within this one Step Functions execution -- run_backtest uses it to key a resumable-progress breadcrumb in S3 (training_common.save_run_progress), so a relaunched attempt skips whichever candidates an earlier attempt already settled instead of redoing them. Retry absorbs a transient reclaim; if it keeps failing, the Catch below falls through to RunTrainingTaskOnDemand (guaranteed on-demand Fargate) rather than giving up on this target for the month.",
                  "Parameters": {
                    "Cluster": "${aws_ecs_cluster.main.arn}",
                    "TaskDefinition.$": "States.Format('${var.project}-{}-{}', $.sport, $.target.M.task_definition_suffix.S)",
                    "CapacityProviderStrategy": [
                      {
                        "CapacityProvider": "FARGATE_SPOT",
                        "Weight": 1
                      }
                    ],
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
                            },
                            {
                              "Name": "TRAINING_RUN_ID",
                              "Value.$": "$$.Execution.Name"
                            }
                          ]
                        }
                      ]
                    }
                  },
                  "Retry": [
                    {
                      "ErrorEquals": ["States.ALL"],
                      "Comment": "Absorbs a transient Spot reclaim -- a genuine failure (a real bug in the training script) just burns these 2 retries before still reaching the Catch below, same eventual outcome as no retry at all.",
                      "MaxAttempts": 2,
                      "IntervalSeconds": 30,
                      "BackoffRate": 2.0
                    }
                  ],
                  "Catch": [
                    {
                      "ErrorEquals": ["States.ALL"],
                      "Next": "RunTrainingTaskOnDemand"
                    }
                  ],
                  "End": true
                },
                "RunTrainingTaskOnDemand": {
                  "Type": "Task",
                  "Resource": "arn:aws:states:::ecs:runTask.sync",
                  "Comment": "Fallback after RunTrainingTask's own Spot attempts were all reclaimed or failed -- guaranteed on-demand capacity, no further fallback after this one. Not budgeted against training_max_concurrency's Spot-sized vCPU math (locals-training-compute.tf) -- a rare path, and the worst case if many targets fall back at once is some of THESE on-demand launches failing on capacity, not a wider outage.",
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
                            },
                            {
                              "Name": "TRAINING_RUN_ID",
                              "Value.$": "$$.Execution.Name"
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
                  "Comment": "One target's training failure doesn't block the rest of this sport's targets -- each target is independently versioned (library/ml/training_common.py), so a failed run here just means this cycle's retrain didn't happen for this one target.",
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

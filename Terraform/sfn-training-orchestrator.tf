# Monthly training orchestrator -- scans the sport registry, rebuilds each
# in-season sport's training dataset (Fargate on-demand), then trains every
# target on EC2 (ec2-training-asg.tf's Spot-primary/on-demand-fallback
# capacity providers). EC2 is the only training compute path.
#
# ForEachSport's MaxConcurrency is not pinned to 1 -- see locals-training-
# compute.tf's comment (EC2's vCPU budget is divided by sport-concurrency
# before being divided by per-task vCPU, so concurrent sports can't
# jointly exceed the same total budget one sport alone would use).
#
# RunTrainingTask/RunTrainingTaskOnDemand's own NetworkConfiguration has
# no AssignPublicIp -- unlike RunFeatureEngineering above (still FARGATE
# launch type, where that field is required), it isn't valid at all for a
# CapacityProviderStrategy task on EC2 launch type. Reachability instead
# comes from the EC2 instance's own public IP, requested at the
# launch-template level (ec2-training-launch-template.tf's
# network_interfaces block) since these public subnets don't auto-assign
# one (map_public_ip_on_launch = false, subnet-public.tf).
#
# Both states' own Overrides.Memory (61440 MiB) trims the task-definition's
# own 65536 MiB (training_task_memory_per_vcpu_mib x training_task_vcpu,
# variables.tf) down for THIS state machine only -- the task definitions
# themselves (ecs-task-*-train-*.tf) are untouched. A "64GB" 4xlarge
# instance never registers a full 65536 MiB of schedulable memory with
# ECS -- the OS/kernel/ECS agent reserve a slice first -- so a task asking
# for the full 65536 MiB can never be placed on an instance of this size.
#
# Both states' own NetworkConfiguration.Subnets are the private subnets
# (aws_subnet.private_a/b/c), not the public ones RunFeatureEngineering
# above uses -- for EC2 launch type + awsvpc mode, the task's own network
# interface is independent of the underlying EC2 host's own subnet
# (ec2-training-asg.tf's ASGs still launch into the public subnets, needed
# for ECR pull/agent registration), so the task's own data-plane traffic
# never needs to touch a public route. It only ever calls S3/DynamoDB via
# vpc-endpoints.tf's Gateway Endpoints, private route table only.
resource "aws_cloudwatch_log_group" "training_orchestrator" {
  name              = "/aws/vendedlogs/states/${var.project}-training-orchestrator"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "training"
  })
}

resource "aws_sfn_state_machine" "training_orchestrator" {
  name     = "${var.project}-training-orchestrator"
  role_arn = aws_iam_role.stepfunctions_orchestrator.arn
  type     = "STANDARD"

  depends_on = [time_sleep.iam_propagation]

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.training_orchestrator.arn}:*"
    include_execution_data = true
    level                  = "ALL"
  }

  # Surfaces this state machine (and, via propagated trace headers, the
  # season_gate/ec2_training_reaper Lambdas it invokes) in the CloudWatch
  # Application Map alongside the existing sports-predictor-api and
  # standalone-Lambda nodes. Cheap at this cadence: a monthly execution is
  # a handful of traces/month, nowhere near X-Ray's 100k-traces-recorded
  # free tier.
  tracing_configuration {
    enabled = true
  }

  definition = <<EOF
{
  "Comment": "Scans the sport registry, rebuilds each in-season sport's training dataset on Fargate on-demand, then trains every target on EC2 (Spot-primary, on-demand fallback). Monthly, via scheduler-training-orchestrator.tf.",
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
      "MaxConcurrency": ${local.training_sport_concurrency},
      "Comment": "Not pinned to 1 -- locals-training-compute.tf divides the EC2 vCPU budget by this same concurrency before dividing by per-task vCPU, so sports running at once can't jointly exceed it.",
      "ItemProcessor": {
        "ProcessorConfig": {
          "Mode": "INLINE"
        },
        "StartAt": "CheckSeason",
        "States": {
          "CheckSeason": {
            "Type": "Task",
            "Resource": "arn:aws:states:::lambda:invoke",
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
            "Comment": "Stays on Fargate on-demand -- feature-engineering's own vCPU footprint is small and was never the reason sports were serialized; only TrainAllTargets below runs on EC2.",
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
              },
              "Overrides": {
                "ContainerOverrides": [
                  {
                    "Name.$": "States.Format('{}-feature-engineering', $.sport.S)",
                    "Environment": [
                      {
                        "Name": "TRAINING_LOOKBACK_SEASONS",
                        "Value.$": "$.training_lookback_seasons.N"
                      }
                    ]
                  }
                ]
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
            "End": true
          },
          "TrainAllTargets": {
            "Type": "Map",
            "ItemsPath": "$.training_targets.L",
            "MaxConcurrency": ${local.training_max_concurrency},
            "ToleratedFailurePercentage": 100,
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
                  "Comment": "Same TRAINING_RUN_ID/resumability mechanism every training task uses (library/ml/training_common.py) -- only the CapacityProviderStrategy differs from the on-demand fallback below.",
                  "Parameters": {
                    "Cluster": "${aws_ecs_cluster.main.arn}",
                    "TaskDefinition.$": "States.Format('${var.project}-{}-{}', $.sport, $.target.M.task_definition_suffix.S)",
                    "CapacityProviderStrategy": [
                      {
                        "CapacityProvider": "${aws_ecs_capacity_provider.ec2_training_spot.name}",
                        "Weight": 1
                      }
                    ],
                    "PropagateTags": "TASK_DEFINITION",
                    "NetworkConfiguration": {
                      "AwsvpcConfiguration": {
                        "Subnets": ["${aws_subnet.private_a.id}", "${aws_subnet.private_b.id}", "${aws_subnet.private_c.id}"],
                        "SecurityGroups": ["${aws_security_group.fargate_internet_egress.id}"]
                      }
                    },
                    "Overrides": {
                      "Memory": "61440",
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
                      "MaxAttempts": 2,
                      "IntervalSeconds": 30,
                      "BackoffRate": 2.0
                    }
                  ],
                  "Catch": [
                    {
                      "ErrorEquals": ["States.ALL"],
                      "ResultPath": "$.spot_failure",
                      "Next": "RunTrainingTaskOnDemand"
                    }
                  ],
                  "End": true
                },
                "RunTrainingTaskOnDemand": {
                  "Type": "Task",
                  "Resource": "arn:aws:states:::ecs:runTask.sync",
                  "Parameters": {
                    "Cluster": "${aws_ecs_cluster.main.arn}",
                    "TaskDefinition.$": "States.Format('${var.project}-{}-{}', $.sport, $.target.M.task_definition_suffix.S)",
                    "CapacityProviderStrategy": [
                      {
                        "CapacityProvider": "${aws_ecs_capacity_provider.ec2_training_ondemand.name}",
                        "Weight": 1
                      }
                    ],
                    "PropagateTags": "TASK_DEFINITION",
                    "NetworkConfiguration": {
                      "AwsvpcConfiguration": {
                        "Subnets": ["${aws_subnet.private_a.id}", "${aws_subnet.private_b.id}", "${aws_subnet.private_c.id}"],
                        "SecurityGroups": ["${aws_security_group.fargate_internet_egress.id}"]
                      }
                    },
                    "Overrides": {
                      "Memory": "61440",
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
                  "End": true
                }
              }
            },
            "End": true
          }
        }
      },
      "Next": "ScaleDownTrainingSpotCapacity"
    },
    "ScaleDownTrainingSpotCapacity": {
      "Type": "Task",
      "Resource": "arn:aws:states:::aws-sdk:autoscaling:setDesiredCapacity",
      "Comment": "Explicitly scales both ASGs back to 0 the moment every sport's ForEachSport iteration finishes, rather than waiting on ECS managed_scaling's own reactive scale-in -- a real run left instances sitting InService and billing for 30-40+ minutes after the execution itself had already SUCCEEDED, since nothing tells the ASG the demand is gone until its own target-tracking policy notices on its own (deliberately conservative, slower to scale in than out, and not tunable via the capacity provider's own managed_scaling arguments). Best-effort: a failure here doesn't fail the orchestrator run itself, same tolerant pattern TrainingTaskFailed above already uses. lambda-ec2-training-reaper.tf is the independent backstop for this same problem when a run is stopped manually instead of finishing normally -- this state alone can't cover that case, since a manually-stopped execution runs no further states at all. See iam-stepfunctions-orchestrator.tf for the autoscaling:SetDesiredCapacity grant this needs.",
      "Parameters": {
        "AutoScalingGroupName": "${aws_autoscaling_group.ec2_training_spot.name}",
        "DesiredCapacity": 0,
        "HonorCooldown": false
      },
      "ResultPath": null,
      "Catch": [
        {
          "ErrorEquals": ["States.ALL"],
          "ResultPath": "$.scale_down_spot_error",
          "Next": "ScaleDownTrainingOnDemandCapacity"
        }
      ],
      "Next": "ScaleDownTrainingOnDemandCapacity"
    },
    "ScaleDownTrainingOnDemandCapacity": {
      "Type": "Task",
      "Resource": "arn:aws:states:::aws-sdk:autoscaling:setDesiredCapacity",
      "Parameters": {
        "AutoScalingGroupName": "${aws_autoscaling_group.ec2_training_ondemand.name}",
        "DesiredCapacity": 0,
        "HonorCooldown": false
      },
      "ResultPath": null,
      "Catch": [
        {
          "ErrorEquals": ["States.ALL"],
          "ResultPath": "$.scale_down_ondemand_error",
          "Next": "TrainingOrchestratorDone"
        }
      ],
      "Next": "TrainingOrchestratorDone"
    },
    "TrainingOrchestratorDone": {
      "Type": "Pass",
      "End": true
    }
  }
}
EOF

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "training"
  })
}

# Second, parallel training path -- same registry-driven shape as
# sfn-training-orchestrator.tf, but TrainAllTargets launches against the
# EC2 training track (ec2-training-asg.tf) instead of Fargate/Fargate
# Spot. Built to be A/B-tested against the Fargate orchestrator, which
# stays completely untouched (same schedule, same capacity providers, same
# concurrency) -- nothing here is wired into it or replaces it.
#
# Deliberately NOT on an EventBridge schedule -- unlike training_orchestrator,
# this only ever runs when manually started (`aws stepfunctions
# start-execution --state-machine-arn <arn> --input '{}'`), so real EC2
# cost/duration numbers can be gathered before this is trusted to run
# unattended. Every resource this state machine touches is tagged
# Component = "training-ec2-canary" (distinct from "training") so Cost
# Explorer can isolate its real spend from the Fargate path's.
#
# ForEachSport's MaxConcurrency is NOT pinned to 1 the way the Fargate
# orchestrator's is -- see locals-training-compute-ec2.tf's comment for
# why that's safe here (EC2's vCPU budget is divided by sport-concurrency
# before being divided by per-task vCPU, so concurrent sports can't
# jointly exceed the same total budget one sport alone would use).
#
# RunTrainingTaskEc2Spot/RunTrainingTaskEc2OnDemand's own
# NetworkConfiguration has no AssignPublicIp -- unlike RunFeatureEngineering
# above (still FARGATE launch type, where that field is required), it isn't
# valid at all for a CapacityProviderStrategy task on EC2 launch type
# ("ECS.InvalidParameterException: Assign public IP is not supported for
# this launch type" -- a real canary run's first-ever execution failed
# 100% of its 42 training targets on exactly this, silently, since
# TrainingTaskFailed's own tolerant Pass state let the execution report
# SUCCEEDED anyway). Reachability instead comes from the EC2 instance's
# OWN public IP, requested at the launch-template level
# (ec2-training-launch-template.tf's network_interfaces block) since these
# public subnets don't auto-assign one (map_public_ip_on_launch = false,
# subnet-public.tf).
#
# Both states' own Overrides.Memory (61440 MiB) trims the task-definition's
# own 65536 MiB (training_task_memory_per_vcpu_mib x training_task_vcpu,
# variables.tf) down for THIS state machine only -- the task definitions
# themselves (ecs-task-*-train-*.tf) are untouched, so Fargate's own
# training_orchestrator still launches every task at the full 65536 MiB it
# always has. EC2-on-EC2 needs this override because a real "64GB" 4xlarge
# instance never actually registers a full 65536 MiB of schedulable memory
# with ECS -- the OS/kernel/ECS agent reserve a slice first (a real m7a.
# 4xlarge in this account registered 62924 MiB, confirmed via
# describe-container-instances during the second canary run) -- so a task
# still asking for the full 65536 MiB can never be placed on ANY instance
# of this size, no matter how long the capacity provider waits. 61440
# leaves a margin below the observed 62924 ceiling for cross-instance-type
# variance (m7i/m6i/m7a/m6a) without giving back more of the RF-model's
# own OOM-driven 64GB budget (ec2-training-launch-template.tf's own
# comment) than this constraint actually forces.
resource "aws_cloudwatch_log_group" "training_orchestrator_ec2" {
  name              = "/aws/vendedlogs/states/${var.project}-training-orchestrator-ec2"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "training-ec2-canary"
  })
}

resource "aws_sfn_state_machine" "training_orchestrator_ec2" {
  name     = "${var.project}-training-orchestrator-ec2"
  role_arn = aws_iam_role.stepfunctions_orchestrator.arn
  type     = "STANDARD"

  depends_on = [time_sleep.iam_propagation]

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.training_orchestrator_ec2.arn}:*"
    include_execution_data = true
    level                  = "ALL"
  }

  definition = <<EOF
{
  "Comment": "EC2 training track (canary) -- scans the sport registry, rebuilds each in-season sport's training dataset on Fargate (unchanged), then trains every target on the EC2 capacity providers instead of Fargate Spot/Fargate. Manually invoked only -- no EventBridge schedule.",
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
      "MaxConcurrency": ${local.ec2_training_sport_concurrency},
      "Comment": "Not pinned to 1 -- locals-training-compute-ec2.tf divides the EC2 vCPU budget by this same concurrency before dividing by per-task vCPU, so sports running at once can't jointly exceed it.",
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
            "Comment": "Stays on Fargate on-demand, unchanged from the Fargate orchestrator -- feature-engineering's own vCPU footprint is small and was never the reason sports were serialized; only TrainAllTargets below moves to the EC2 track.",
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
            "MaxConcurrency": ${local.ec2_training_target_concurrency},
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
              "StartAt": "RunTrainingTaskEc2Spot",
              "States": {
                "RunTrainingTaskEc2Spot": {
                  "Type": "Task",
                  "Resource": "arn:aws:states:::ecs:runTask.sync",
                  "Comment": "Same task definitions the Fargate orchestrator uses (now EC2-compatible too, see ecs-task-*-train-*.tf) and the same TRAINING_RUN_ID/resumability mechanism (library/ml/training_common.py) -- only the CapacityProviderStrategy differs.",
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
                        "Subnets": ["${aws_subnet.public_1.id}", "${aws_subnet.public_2.id}", "${aws_subnet.public_3.id}"],
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
                      "Next": "RunTrainingTaskEc2OnDemand"
                    }
                  ],
                  "End": true
                },
                "RunTrainingTaskEc2OnDemand": {
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
                        "Subnets": ["${aws_subnet.public_1.id}", "${aws_subnet.public_2.id}", "${aws_subnet.public_3.id}"],
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
      "End": true
    }
  }
}
EOF

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "training-ec2-canary"
  })
}

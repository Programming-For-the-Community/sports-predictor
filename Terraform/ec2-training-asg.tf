# EC2 training track's compute -- two ASGs (Spot-primary, on-demand
# fallback) on the same launch template (ec2-training-launch-template.tf),
# each wrapped in its own ECS capacity provider so
# sfn-training-orchestrator-ec2.tf can launch tasks against them exactly
# the way sfn-training-orchestrator.tf already launches against
# FARGATE_SPOT/FARGATE -- same ecs:runTask.sync integration, same
# Retry/Catch shape, just a different CapacityProviderStrategy.
#
# min_size = 0 on both: managed_scaling below only launches an instance
# once a task actually needs placement, and managed_termination_protection
# is what tears an instance back down the moment its task finishes --
# "terminated once training is complete" for free, with no custom
# shutdown script.
#
# Instance-type diversification (m7i/m6i newer-gen Intel, m7a/m6a AMD) --
# all 4 are the same 16 vCPU / 64 GB shape Fargate training already uses
# (general-purpose, not compute-optimized, since the RF-model OOM history
# already consumed the full 64 GB budget once), chosen for stronger
# per-core throughput on the CPU-bound XGBoost/LightGBM/sklearn workloads
# than the equivalent older-gen m5, and for the Spot-interruption-rate
# benefit of drawing from more than one capacity pool -- something
# Fargate Spot structurally can't offer since it has no instance-type
# selection at all.
locals {
  ec2_training_instance_types = ["m7i.4xlarge", "m6i.4xlarge", "m7a.4xlarge", "m6a.4xlarge"]
}

resource "aws_autoscaling_group" "ec2_training_spot" {
  name                  = "${var.project}-ec2-training-spot"
  vpc_zone_identifier   = [aws_subnet.public_1.id, aws_subnet.public_2.id, aws_subnet.public_3.id]
  min_size              = 0
  max_size              = local.ec2_training_target_concurrency
  protect_from_scale_in = true # required for managed_termination_protection below

  mixed_instances_policy {
    launch_template {
      launch_template_specification {
        launch_template_id = aws_launch_template.ec2_training.id
        version            = "$Latest"
      }

      dynamic "override" {
        for_each = local.ec2_training_instance_types
        content {
          instance_type = override.value
        }
      }
    }

    instances_distribution {
      on_demand_base_capacity                  = 0
      on_demand_percentage_above_base_capacity = 0 # 100% Spot
      spot_allocation_strategy                 = "capacity-optimized-prioritized"
    }
  }

  tag {
    key                 = "Component"
    value               = "training-ec2-canary"
    propagate_at_launch = true
  }
  tag {
    key                 = "Sport"
    value               = "shared"
    propagate_at_launch = true
  }
  tag {
    key                 = "Project"
    value               = var.project
    propagate_at_launch = true
  }
  tag {
    key                 = "Owner"
    value               = var.owner
    propagate_at_launch = true
  }
  tag {
    key                 = "Environment"
    value               = var.environment
    propagate_at_launch = true
  }
}

# Guaranteed on-demand fallback -- same relationship RunTrainingTaskOnDemand
# already has to RunTrainingTask's Spot attempt. Not sized against
# local.ec2_training_target_concurrency -- a rare path, same reasoning
# the Fargate on-demand fallback's own comment gives (sfn-training-
# orchestrator.tf).
resource "aws_autoscaling_group" "ec2_training_ondemand" {
  name                  = "${var.project}-ec2-training-ondemand"
  vpc_zone_identifier   = [aws_subnet.public_1.id, aws_subnet.public_2.id, aws_subnet.public_3.id]
  min_size              = 0
  max_size              = 1
  protect_from_scale_in = true

  mixed_instances_policy {
    launch_template {
      launch_template_specification {
        launch_template_id = aws_launch_template.ec2_training.id
        version            = "$Latest"
      }

      dynamic "override" {
        for_each = local.ec2_training_instance_types
        content {
          instance_type = override.value
        }
      }
    }

    instances_distribution {
      on_demand_base_capacity                  = 0
      on_demand_percentage_above_base_capacity = 100
    }
  }

  tag {
    key                 = "Component"
    value               = "training-ec2-canary"
    propagate_at_launch = true
  }
  tag {
    key                 = "Sport"
    value               = "shared"
    propagate_at_launch = true
  }
  tag {
    key                 = "Project"
    value               = var.project
    propagate_at_launch = true
  }
  tag {
    key                 = "Owner"
    value               = var.owner
    propagate_at_launch = true
  }
  tag {
    key                 = "Environment"
    value               = var.environment
    propagate_at_launch = true
  }
}

resource "aws_ecs_capacity_provider" "ec2_training_spot" {
  name = "${var.project}-ec2-training-spot"

  auto_scaling_group_provider {
    auto_scaling_group_arn         = aws_autoscaling_group.ec2_training_spot.arn
    managed_termination_protection = "ENABLED"

    managed_scaling {
      status                    = "ENABLED"
      target_capacity           = 100
      minimum_scaling_step_size = 1
      maximum_scaling_step_size = 1
    }
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "training-ec2-canary"
  })
}

resource "aws_ecs_capacity_provider" "ec2_training_ondemand" {
  name = "${var.project}-ec2-training-ondemand"

  auto_scaling_group_provider {
    auto_scaling_group_arn         = aws_autoscaling_group.ec2_training_ondemand.arn
    managed_termination_protection = "ENABLED"

    managed_scaling {
      status                    = "ENABLED"
      target_capacity           = 100
      minimum_scaling_step_size = 1
      maximum_scaling_step_size = 1
    }
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "training-ec2-canary"
  })
}

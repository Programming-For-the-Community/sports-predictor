# EC2 training track's compute -- two ASGs (Spot-primary, on-demand
# fallback) on the same launch template (ec2-training-launch-template.tf),
# each wrapped in its own ECS capacity provider so
# sfn-training-orchestrator.tf can launch tasks against them exactly
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
  name                = "${var.project}-ec2-training-spot"
  vpc_zone_identifier = [aws_subnet.public_1.id, aws_subnet.public_2.id, aws_subnet.public_3.id]
  min_size            = 0
  # The AGGREGATE ceiling (locals-training-compute.tf), not
  # training_max_concurrency -- that's one sport's own 1/N share of the
  # budget, but this one fleet is shared by every sport running at once,
  # so it needs headroom for all of them combined, not just one.
  max_size              = local.training_max_instances
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
      # price-capacity-optimized (AWS's own current recommended default,
      # since late 2023), not capacity-optimized-prioritized -- that
      # strategy picks purely for lowest interruption risk and never
      # considers price at all, confirmed picking m7a.4xlarge for 100% of
      # a real run's instances while m6i.4xlarge's own real Spot price sat
      # at roughly half of every other candidate's that same night. This
      # still screens out genuinely volatile/high-interruption pools
      # (unlike plain lowest-price, which doesn't), just no longer ignores
      # price entirely the way the prior strategy did.
      spot_allocation_strategy = "price-capacity-optimized"
    }
  }

  tag {
    key                 = "Component"
    value               = "training"
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
# local.training_max_concurrency -- a rare path, same reasoning
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
    value               = "training"
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
    Component = "training"
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
    Component = "training"
  })
}

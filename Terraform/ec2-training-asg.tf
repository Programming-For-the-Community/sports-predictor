# Training compute -- two ASGs (Spot-primary, on-demand fallback) on the
# same launch template (ec2-training-launch-template.tf), each wrapped in
# its own ECS capacity provider for sfn-training-orchestrator.tf's
# ecs:runTask.sync. min_size = 0: managed_scaling launches an instance
# only when a task needs placement; managed_termination_protection
# terminates it once the task finishes.
#
# 4 instance types, all 16 vCPU / 64 GB, for Spot capacity-pool diversity.
locals {
  ec2_training_instance_types = ["m7i.4xlarge", "m6i.4xlarge", "m7a.4xlarge", "m6a.4xlarge"]
}

resource "aws_autoscaling_group" "ec2_training_spot" {
  name                = "${var.project}-ec2-training-spot"
  vpc_zone_identifier = [aws_subnet.public_1.id, aws_subnet.public_2.id, aws_subnet.public_3.id]
  min_size            = 0
  # Aggregate ceiling across all sports (locals-training-compute.tf) --
  # this fleet is shared, not per-sport.
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
      spot_allocation_strategy                 = "price-capacity-optimized"
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

# On-demand fallback for RunTrainingTaskOnDemand.
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

# managed_scaling auto-creates 2 CloudWatch alarms per capacity provider
# (TargetTracking-<name>-Alarm{High,Low}-<uuid>) with no Terraform
# resource of their own -- tagged by scripts/tag_capacity_provider_alarms.py,
# see .github/workflows/tf_install.yml.
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


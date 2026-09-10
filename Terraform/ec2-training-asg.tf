# Training compute -- two ASGs (Spot-primary, on-demand fallback) on the
# same launch template (ec2-training-launch-template.tf), each wrapped in
# its own ECS capacity provider so sfn-training-orchestrator.tf can
# launch tasks against them via ecs:runTask.sync.
#
# min_size = 0 on both: managed_scaling below only launches an instance
# once a task actually needs placement, and managed_termination_protection
# is what tears an instance back down the moment its task finishes --
# "terminated once training is complete" for free, with no custom
# shutdown script.
#
# Instance-type diversification (m7i/m6i newer-gen Intel, m7a/m6a AMD) --
# all 4 are the same 16 vCPU / 64 GB general-purpose shape, drawing from
# more than one Spot capacity pool.
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
      # Screens out volatile/high-interruption pools, then picks by price
      # among what's left.
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

# managed_scaling below (on both capacity providers) makes AWS Application
# Auto Scaling create a target-tracking scaling policy behind the scenes,
# which in turn auto-creates 2 CloudWatch alarms per capacity provider
# (TargetTracking-<capacity-provider-name>-Alarm{High,Low}-<random-uuid>).
# There's no Terraform resource for these at all -- they're a side effect
# of managed_scaling, and their name's random suffix means there's no
# stable ARN to reference declaratively even via a separate resource, so
# neither aws_ecs_capacity_provider resource below can carry a tags block
# that reaches them. scripts/tag_capacity_provider_alarms.py (run via the
# local-exec provisioners right below, once per capacity provider) finds
# them by name prefix instead and tags them with the same common_tags
# every other resource in this file gets -- so a fresh `terraform apply`
# keeps them tagged with no manual step, including after either capacity
# provider is replaced and gets a new pair of alarms with fresh random
# suffixes. Found untagged in a CloudWatch tag audit (2026-09-09).
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

# Tags the 2 auto-created scaling alarms for each capacity provider above
# (see its own comment) -- runs scripts/tag_capacity_provider_alarms.py as
# part of `terraform apply` itself, not a separate manual step.
# triggers_replace ties each to its own capacity provider's id, so a
# replacement (which regenerates both alarms with fresh random-suffixed
# names) re-runs this and re-tags the new pair; an in-place update that
# leaves the capacity provider's id unchanged does not, since the existing
# alarms and their tags are untouched by that kind of update anyway.
resource "terraform_data" "tag_ec2_training_scaling_alarms" {
  for_each = {
    ec2_training_spot     = aws_ecs_capacity_provider.ec2_training_spot
    ec2_training_ondemand = aws_ecs_capacity_provider.ec2_training_ondemand
  }

  triggers_replace = [each.value.id]

  provisioner "local-exec" {
    command = join(" ", [
      "python3", "${path.module}/scripts/tag_capacity_provider_alarms.py",
      each.value.name, var.region,
      "Project=${var.project}", "Owner=${var.owner}", "Environment=${var.environment}",
      "Sport=shared", "Component=training",
    ])
  }
}

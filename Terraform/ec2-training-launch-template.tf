# Launch template shared by both EC2 training ASGs (ec2-training-asg.tf).
# Always resolves to the latest ECS-optimized AMI at apply time rather
# than pinning a stale one -- same reasoning training's container images
# already use a floating "-latest" tag for (docker_build_push.yml).
data "aws_ssm_parameter" "ecs_optimized_ami" {
  name = "/aws/service/ecs/optimized-ami/amazon-linux-2023/recommended"
}

resource "aws_launch_template" "ec2_training" {
  name_prefix = "${var.project}-ec2-training-"

  image_id = jsondecode(data.aws_ssm_parameter.ecs_optimized_ami.value)["image_id"]
  iam_instance_profile {
    arn = aws_iam_instance_profile.ec2_training_instance.arn
  }

  # Same SG Fargate training already uses (ECS agent traffic -- cluster
  # control-plane API, ECR, CloudWatch Logs -- plus the task's own ENI SG
  # comes from RunTask's own NetworkConfiguration, sfn-training-
  # orchestrator-ec2.tf, same as Fargate). Set inside network_interfaces,
  # not the top-level vpc_security_group_ids, because EC2 rejects a launch
  # template that sets both.
  #
  # associate_public_ip_address = true is what actually gives this
  # instance internet reachability: ec2-training-asg.tf's ASGs sit in the
  # public subnets (aws_subnet.public_1/2/3), but those subnets don't
  # auto-assign a public IP (map_public_ip_on_launch = false, subnet-
  # public.tf) -- Fargate's own training tasks cover the equivalent gap
  # with AssignPublicIp on the task's own ENI, which isn't a valid
  # parameter for EC2 launch type (see sfn-training-orchestrator-ec2.tf's
  # own comment on RunTrainingTaskEc2Spot/OnDemand -- their real first
  # run failed 100% of its targets on exactly this before this was added).
  network_interfaces {
    device_index                = 0
    associate_public_ip_address = true
    security_groups             = [aws_security_group.fargate_internet_egress.id]
  }

  metadata_options {
    http_tokens   = "required" # IMDSv2 only
    http_endpoint = "enabled"
  }

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size           = 30
      volume_type           = "gp3"
      encrypted             = true
      delete_on_termination = true
    }
  }

  # ECS_ENABLE_SPOT_INSTANCE_DRAINING lets a Spot 2-minute reclaim notice
  # drain the running task gracefully (same intent as Fargate Spot's own
  # reclaim handling) instead of a hard kill.
  user_data = base64encode(<<-EOF
    #!/bin/bash
    echo "ECS_CLUSTER=${aws_ecs_cluster.main.name}" >> /etc/ecs/ecs.config
    echo "ECS_ENABLE_SPOT_INSTANCE_DRAINING=true" >> /etc/ecs/ecs.config
  EOF
  )

  tag_specifications {
    resource_type = "instance"
    tags = merge(local.common_tags, {
      Sport     = "shared"
      Component = "training-ec2-canary"
    })
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "training-ec2-canary"
  })

  lifecycle {
    create_before_destroy = true
  }
}

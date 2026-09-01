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

  # awsvpc network mode (same as every Fargate training task) still needs
  # this instance-level SG for the ECS agent's own traffic (cluster
  # control-plane API, ECR, CloudWatch Logs) -- identical shape to what
  # training already uses on Fargate, no new SG needed. The task's own
  # ENI gets its SG from RunTask's NetworkConfiguration
  # (sfn-training-orchestrator-ec2.tf), same as Fargate.
  vpc_security_group_ids = [aws_security_group.fargate_internet_egress.id]

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

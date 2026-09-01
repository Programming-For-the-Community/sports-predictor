# Instance role/profile for the EC2 training track's own EC2 instances
# (ec2-training-launch-template.tf). Separate from aws_iam_role.ecs_pipeline
# (iam-ecs-pipeline.tf) on purpose: this role is only ever assumed by the
# ECS agent running on the instance itself (to register/deregister with
# the cluster, pull images, ship logs), never by a task. The task's own
# DynamoDB/S3 permissions stay on aws_iam_role.ecs_pipeline, reused as-is
# for task_role_arn on the "EC2"-compatible training task definitions --
# a task's role is assumed by the task's own execution context regardless
# of EC2 vs. Fargate launch type, so no new grant was needed there.
data "aws_iam_policy_document" "ec2_training_instance_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ec2_training_instance" {
  name               = "${var.project}-ec2-training-instance-role"
  assume_role_policy = data.aws_iam_policy_document.ec2_training_instance_assume.json

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "training-ec2-canary"
  })
}

# AWS-managed policy granting exactly what the ECS agent needs
# (RegisterContainerInstance, DiscoverPollEndpoint, image pulls via ECR,
# log/metric shipping) -- no training-data permissions live here.
resource "aws_iam_role_policy_attachment" "ec2_training_instance_ecs" {
  role       = aws_iam_role.ec2_training_instance.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role"
}

resource "aws_iam_instance_profile" "ec2_training_instance" {
  name = "${var.project}-ec2-training-instance-profile"
  role = aws_iam_role.ec2_training_instance.name

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "training-ec2-canary"
  })
}

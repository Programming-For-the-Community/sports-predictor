# Dedicated role -- ec2:DescribeInstances doesn't support resource-level
# scoping (AWS-wide "*" is the only option for that action), but the
# actual termination action is scoped tightly to the two training ASGs,
# and describe/list-only ECS actions are scoped to the one cluster.
data "aws_iam_policy_document" "lambda_ec2_training_reaper_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda_ec2_training_reaper" {
  name               = "${var.project}-lambda-ec2-training-reaper-exec"
  assume_role_policy = data.aws_iam_policy_document.lambda_ec2_training_reaper_assume.json

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "training"
  })
}

resource "aws_iam_role_policy_attachment" "lambda_ec2_training_reaper_logs" {
  role       = aws_iam_role.lambda_ec2_training_reaper.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# For the function's own tracing_config (mode = "Active", lambda-ec2-
# training-reaper.tf).
resource "aws_iam_role_policy_attachment" "lambda_ec2_training_reaper_xray" {
  role       = aws_iam_role.lambda_ec2_training_reaper.name
  policy_arn = "arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess"
}

data "aws_iam_policy_document" "lambda_ec2_training_reaper_permissions" {
  statement {
    sid       = "DescribeTrainingInstances"
    actions   = ["ec2:DescribeInstances"]
    resources = ["*"]
  }

  statement {
    sid       = "InspectClusterContainerInstances"
    actions   = ["ecs:ListContainerInstances", "ecs:DescribeContainerInstances"]
    resources = [aws_ecs_cluster.main.arn]
  }

  # Same API/reasoning as sfn-training-orchestrator.tf's own scale-down
  # states (iam-stepfunctions-orchestrator.tf's ScaleDownEc2TrainingCapacity
  # statement) -- ShouldDecrementDesiredCapacity=true, not a raw
  # ec2:TerminateInstances, so the ASG doesn't launch a replacement.
  statement {
    sid     = "ReapIdleTrainingInstances"
    actions = ["autoscaling:TerminateInstanceInAutoScalingGroup"]
    resources = [
      "arn:aws:autoscaling:${var.region}:${var.account_id}:autoScalingGroup:*:autoScalingGroupName/${var.project}-ec2-training-spot",
      "arn:aws:autoscaling:${var.region}:${var.account_id}:autoScalingGroup:*:autoScalingGroupName/${var.project}-ec2-training-ondemand",
    ]
  }
}

resource "aws_iam_role_policy" "lambda_ec2_training_reaper_permissions" {
  name   = "${var.project}-lambda-ec2-training-reaper-permissions"
  role   = aws_iam_role.lambda_ec2_training_reaper.id
  policy = data.aws_iam_policy_document.lambda_ec2_training_reaper_permissions.json
}

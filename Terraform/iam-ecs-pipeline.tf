# Used as both the task execution role (ECR pull, log writes -- via the
# attached AWS-managed policy) and the task role (the application code's
# own AWS access) for both the Feature Engineering and Train Model Fargate
# tasks. Both steps share the same permission shape -- read the data
# tables, write the model artifact -- so one role covers both without
# giving either step more access than it needs.
data "aws_iam_policy_document" "ecs_pipeline_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ecs_pipeline" {
  name               = "${var.project}-ecs-pipeline-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_pipeline_assume.json

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "training"
  })
}

resource "aws_iam_role_policy_attachment" "ecs_pipeline_execution" {
  role       = aws_iam_role.ecs_pipeline.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "ecs_pipeline_permissions" {
  statement {
    sid     = "ReadTrainingData"
    actions = ["dynamodb:Query", "dynamodb:GetItem"]
    resources = [
      "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.entities_table}",
      "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.events_table}",
      "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.player_game_stats_table}",
    ]
  }

  statement {
    sid       = "WriteModelArtifacts"
    actions   = ["s3:PutObject"]
    resources = ["arn:aws:s3:::${local.model_artifacts_bucket}/*"]
  }
}

resource "aws_iam_role_policy" "ecs_pipeline_permissions" {
  name   = "${var.project}-ecs-pipeline-permissions"
  role   = aws_iam_role.ecs_pipeline.id
  policy = data.aws_iam_policy_document.ecs_pipeline_permissions.json
}

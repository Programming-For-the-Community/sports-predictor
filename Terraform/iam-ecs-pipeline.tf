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
  # dynamodb:Scan is needed alongside Query/GetItem for entities (looked
  # up one at a time or scanned in bulk depending on caller). A table's
  # GSI is a distinct IAM resource from the table itself, so
  # get_all_events/get_all_player_game_stats/get_all_team_game_stats each
  # need their own /index/* resource below alongside the base table ARN.
  statement {
    sid     = "ReadTrainingData"
    actions = ["dynamodb:Query", "dynamodb:GetItem", "dynamodb:Scan"]
    resources = [
      "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.entities_table}",
      "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.events_table}",
      "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.player_game_stats_table}",
      "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.team_game_stats_table}",
      "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.events_table}/index/*",
      "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.player_game_stats_table}/index/*",
      "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.team_game_stats_table}/index/*",
    ]
  }

  statement {
    sid       = "ReadWriteModelArtifacts"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["arn:aws:s3:::${local.model_artifacts_bucket}/*"]
  }

  # ListBucket (scoped to each sport's own prefix, same pattern as
  # iam-nfl-backfill.tf's raw-data-lake listing) is what lets a training
  # task discover the next version number for whichever model it's
  # training -- it lists <sport>/<model-name>/v*/ and picks max(existing) + 1
  # rather than needing a separate version-tracking resource. Each model
  # (win-probability, score-margin, one per player-prop stat) versions
  # independently -- see Terraform/s3-model-artifacts.tf. This role is
  # shared across every sport's feature-engineering/training tasks, so its
  # prefix list grows by one entry per sport onboarded, not a new role.
  statement {
    sid       = "ListModelArtifactsSportPrefixes"
    actions   = ["s3:ListBucket"]
    resources = ["arn:aws:s3:::${local.model_artifacts_bucket}"]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["nfl/*", "ncaafb/*"]
    }
  }
}

resource "aws_iam_role_policy" "ecs_pipeline_permissions" {
  name   = "${var.project}-ecs-pipeline-permissions"
  role   = aws_iam_role.ecs_pipeline.id
  policy = data.aws_iam_policy_document.ecs_pipeline_permissions.json
}

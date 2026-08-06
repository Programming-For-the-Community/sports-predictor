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
  # dynamodb:Scan is needed alongside Query/GetItem because
  # get_all_player_game_stats/get_all_team_game_stats scan their base
  # tables directly (no GSI exists for either) rather than issuing one
  # Query per team/player. get_all_events is different -- it Queries the
  # events table's status-index GSI (FeatureStorage.get_all_events,
  # Terraform/dynamodb-events.tf), not a Scan, so it needs the separate
  # /index/* resource below.
  statement {
    sid     = "ReadTrainingData"
    actions = ["dynamodb:Query", "dynamodb:GetItem", "dynamodb:Scan"]
    resources = [
      "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.entities_table}",
      "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.events_table}",
      "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.player_game_stats_table}",
      "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.team_game_stats_table}",
      # A table's GSI is a distinct IAM resource from the table itself --
      # confirmed live via CloudWatch (AccessDeniedException on
      # get_all_events' status-index Query) that the base events_table
      # ARN above doesn't also cover its own GSI, the same gotcha
      # iam-lambda-inference.tf already documents and was already fixed
      # for there.
      "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.events_table}/index/*",
    ]
  }

  statement {
    sid       = "ReadWriteModelArtifacts"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["arn:aws:s3:::${local.model_artifacts_bucket}/*"]
  }

  # ListBucket (scoped to the nfl/ prefix, same pattern as
  # iam-nfl-backfill.tf's raw-data-lake listing) is what lets the training
  # task discover the next version number for whichever model it's
  # training -- it lists nfl/<model-name>/v*/ and picks max(existing) + 1
  # rather than needing a separate version-tracking resource. Each model
  # (win-probability, score-margin, one per player-prop stat) versions
  # independently -- see Terraform/s3-model-artifacts.tf.
  statement {
    sid       = "ListModelArtifactsNflPrefix"
    actions   = ["s3:ListBucket"]
    resources = ["arn:aws:s3:::${local.model_artifacts_bucket}"]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["nfl/*"]
    }
  }
}

resource "aws_iam_role_policy" "ecs_pipeline_permissions" {
  name   = "${var.project}-ecs-pipeline-permissions"
  role   = aws_iam_role.ecs_pipeline.id
  policy = data.aws_iam_policy_document.ecs_pipeline_permissions.json
}

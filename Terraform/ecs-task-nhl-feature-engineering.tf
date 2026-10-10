# 30-day retention so logs don't grow unbounded.
resource "aws_cloudwatch_log_group" "nhl_feature_engineering" {
  name              = "/ecs/${var.project}-nhl-feature-engineering"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "nhl"
    Component = "training"
  })
}

# Standalone Fargate task, run by the training orchestrator. Reads the
# full events/team_game_stats/player_game_stats history and writes the
# event, goalie and player training Parquet files to the model artifacts
# bucket.
# Uses the shared aws_iam_role.ecs_pipeline, whose ListBucket condition
# includes the nhl/* prefix.
resource "aws_ecs_task_definition" "nhl_feature_engineering" {
  family                   = "${var.project}-nhl-feature-engineering"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.feature_engineering_task_cpu["nhl"])
  memory                   = tostring(local.feature_engineering_task_memory["nhl"])
  execution_role_arn       = aws_iam_role.ecs_pipeline.arn
  task_role_arn            = aws_iam_role.ecs_pipeline.arn

  container_definitions = jsonencode([
    {
      name      = "nhl-feature-engineering"
      image     = "${var.ecr_repo_url}:nhl-feature-engineering-latest"
      essential = true
      environment = [
        { name = "AWS_ACCOUNT_ID", value = var.account_id },
        # FeatureStorage's constructor requires all four table names
        # unconditionally.
        { name = "ENTITIES_TABLE_NAME", value = aws_dynamodb_table.entities.name },
        { name = "EVENTS_TABLE_NAME", value = aws_dynamodb_table.events.name },
        { name = "PLAYER_GAME_STATS_TABLE_NAME", value = aws_dynamodb_table.player_game_stats.name },
        { name = "TEAM_GAME_STATS_TABLE_NAME", value = aws_dynamodb_table.team_game_stats.name },
        { name = "MODEL_ARTIFACTS_BUCKET_NAME", value = aws_s3_bucket.model_artifacts.bucket },
        { name = "AWS_REGION", value = var.region },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.nhl_feature_engineering.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "feature-engineering"
        }
      }
    }
  ])

  tags = merge(local.common_tags, {
    Sport     = "nhl"
    Component = "training"
  })
}

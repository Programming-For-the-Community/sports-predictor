# 30-day retention so logs don't grow unbounded.
resource "aws_cloudwatch_log_group" "ncaambb_feature_engineering" {
  name              = "/ecs/${var.project}-ncaambb-feature-engineering"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "ncaambb"
    Component = "training"
  })
}

# Standalone Fargate task. Reads the full events/player_game_stats history
# and writes training Parquet files to the model artifacts bucket. Also
# reads raw AP-poll data directly from the raw data lake (RAW_BUCKET_NAME,
# ncaambb/rankings/* prefix) for the ranking-model dataset -- this is the
# one feature-engineering task that reads the raw bucket at all, since
# NBA/NCAAFB's own build_dataset.py have no such source. Uses the shared
# aws_iam_role.ecs_pipeline, whose ListBucket condition includes the
# ncaambb/* prefix (model artifacts) and ReadRawRankingPolls statement
# (raw bucket, iam-ecs-pipeline.tf).
resource "aws_ecs_task_definition" "ncaambb_feature_engineering" {
  family                   = "${var.project}-ncaambb-feature-engineering"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.feature_engineering_task_cpu["ncaambb"])
  memory                   = tostring(local.feature_engineering_task_memory["ncaambb"])
  execution_role_arn       = aws_iam_role.ecs_pipeline.arn
  task_role_arn            = aws_iam_role.ecs_pipeline.arn

  container_definitions = jsonencode([
    {
      name      = "ncaambb-feature-engineering"
      image     = "${var.ecr_repo_url}:ncaambb-feature-engineering-latest"
      essential = true
      environment = [
        # FeatureStorage's constructor requires all four table names
        # unconditionally.
        { name = "ENTITIES_TABLE_NAME", value = aws_dynamodb_table.entities.name },
        { name = "EVENTS_TABLE_NAME", value = aws_dynamodb_table.events.name },
        { name = "PLAYER_GAME_STATS_TABLE_NAME", value = aws_dynamodb_table.player_game_stats.name },
        { name = "TEAM_GAME_STATS_TABLE_NAME", value = aws_dynamodb_table.team_game_stats.name },
        { name = "MODEL_ARTIFACTS_BUCKET_NAME", value = aws_s3_bucket.model_artifacts.bucket },
        { name = "RAW_BUCKET_NAME", value = aws_s3_bucket.raw_data_lake.bucket },
        { name = "AWS_REGION", value = var.region },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.ncaambb_feature_engineering.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "feature-engineering"
        }
      }
    }
  ])

  tags = merge(local.common_tags, {
    Sport     = "ncaambb"
    Component = "training"
  })
}

# 30-day retention, same rationale as ecs-task-nfl-backfill.tf.
resource "aws_cloudwatch_log_group" "nba_feature_engineering" {
  name              = "/ecs/${var.project}-nba-feature-engineering"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "nba"
    Component = "training"
  })
}

# Standalone Fargate task, same shape as ecs-task-ncaafb-feature-engineering.tf.
# Reads the full events/player_game_stats history and writes training
# Parquet files to the model artifacts bucket -- see
# Source/feature-engineering/nba/build_dataset.py. Uses the shared
# aws_iam_role.ecs_pipeline (iam-ecs-pipeline.tf), whose ListBucket
# condition now includes the nba/* prefix.
resource "aws_ecs_task_definition" "nba_feature_engineering" {
  family                   = "${var.project}-nba-feature-engineering"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.feature_engineering_task_cpu["nba"])
  memory                   = tostring(local.feature_engineering_task_memory["nba"])
  execution_role_arn       = aws_iam_role.ecs_pipeline.arn
  task_role_arn            = aws_iam_role.ecs_pipeline.arn

  container_definitions = jsonencode([
    {
      name      = "nba-feature-engineering"
      image     = "${var.ecr_repo_url}:nba-feature-engineering-latest"
      essential = true
      environment = [
        # FeatureStorage's constructor requires all four table names
        # unconditionally, same as ecs-task-nfl-feature-engineering.tf's
        # own comment.
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
          "awslogs-group"         = aws_cloudwatch_log_group.nba_feature_engineering.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "feature-engineering"
        }
      }
    }
  ])

  tags = merge(local.common_tags, {
    Sport     = "nba"
    Component = "training"
  })
}

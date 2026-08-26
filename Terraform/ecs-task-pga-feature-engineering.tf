# 30-day retention so logs don't grow unbounded.
resource "aws_cloudwatch_log_group" "pga_feature_engineering" {
  name              = "/ecs/${var.project}-pga-feature-engineering"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "pga"
    Component = "training"
  })
}

# Standalone Fargate task. Reads the full events history and writes one
# training Parquet file to the model artifacts bucket. Uses the shared
# aws_iam_role.ecs_pipeline (iam-ecs-pipeline.tf), whose ListBucket
# condition includes the pga/* prefix.
resource "aws_ecs_task_definition" "pga_feature_engineering" {
  family                   = "${var.project}-pga-feature-engineering"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.feature_engineering_task_cpu["pga"])
  memory                   = tostring(local.feature_engineering_task_memory["pga"])
  execution_role_arn       = aws_iam_role.ecs_pipeline.arn
  task_role_arn            = aws_iam_role.ecs_pipeline.arn

  container_definitions = jsonencode([
    {
      name      = "pga-feature-engineering"
      image     = "${var.ecr_repo_url}:pga-feature-engineering-latest"
      essential = true
      environment = [
        # FeatureStorage's constructor requires all four table names
        # unconditionally, even though build_golfer_dataset (Source/
        # feature-engineering/pga/build_dataset.py) only ever calls
        # get_all_events -- a field-event sport has no player_game_stats/
        # team_game_stats table to read at all (design/DATA_SCHEMA.md).
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
          "awslogs-group"         = aws_cloudwatch_log_group.pga_feature_engineering.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "feature-engineering"
        }
      }
    }
  ])

  tags = merge(local.common_tags, {
    Sport     = "pga"
    Component = "training"
  })
}

# 30-day retention so logs don't grow unbounded.
resource "aws_cloudwatch_log_group" "f1_feature_engineering" {
  name              = "/ecs/${var.project}-f1-feature-engineering"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "f1"
    Component = "training"
  })
}

# Standalone Fargate task. Reads the full events history (driver-race,
# constructor-race, and Sprint-race events) and writes up to three
# training Parquet files to the model artifacts bucket -- sprint_features.
# parquet is skipped, not written, when there's no Sprint race data yet
# (see feature-engineering/f1/build_dataset.py's own main() docstring).
# Uses the shared aws_iam_role.ecs_pipeline (iam-ecs-pipeline.tf), whose
# ListModelArtifactsSportPrefixes condition includes the f1/* prefix --
# unlike PGA/NCAA MBB, F1's own build_dataset.py reads nothing from the
# raw data lake at all (no season-stats-style raw snapshot dependency),
# so no ReadRawRankingPolls-equivalent grant was needed here.
resource "aws_ecs_task_definition" "f1_feature_engineering" {
  family                   = "${var.project}-f1-feature-engineering"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.feature_engineering_task_cpu["f1"])
  memory                   = tostring(local.feature_engineering_task_memory["f1"])
  execution_role_arn       = aws_iam_role.ecs_pipeline.arn
  task_role_arn            = aws_iam_role.ecs_pipeline.arn

  container_definitions = jsonencode([
    {
      name      = "f1-feature-engineering"
      image     = "${var.ecr_repo_url}:f1-feature-engineering-latest"
      essential = true
      environment = [
        # FeatureStorage's constructor requires all four table names
        # unconditionally, even though build_driver_dataset/build_
        # constructor_dataset/build_sprint_dataset (Source/feature-
        # engineering/f1/build_dataset.py) only ever call get_all_events
        # -- a field-event sport has no player_game_stats/team_game_stats
        # table to read at all (design/DATA_SCHEMA.md).
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
          "awslogs-group"         = aws_cloudwatch_log_group.f1_feature_engineering.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "feature-engineering"
        }
      }
    }
  ])

  tags = merge(local.common_tags, {
    Sport     = "f1"
    Component = "training"
  })
}

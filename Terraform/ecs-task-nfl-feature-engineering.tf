# 30-day log retention.
resource "aws_cloudwatch_log_group" "nfl_feature_engineering" {
  name              = "/ecs/${var.project}-nfl-feature-engineering"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "nfl"
    Component = "training"
  })
}

# Standalone Fargate task (run via `aws ecs run-task`, not a Service). Reads
# the full events/player_game_stats history and writes two training Parquet
# files to the model artifacts bucket (Source/feature-engineering/nfl/build_dataset.py).
#
# Uses the shared aws_iam_role.ecs_pipeline (iam-ecs-pipeline.tf), scoped
# to read the three data tables and write model artifacts.
#
# Runs in the public subnet + fargate_internet_egress security group (set
# at `run-task` time, not here -- see security-groups.tf).
resource "aws_ecs_task_definition" "nfl_feature_engineering" {
  family                   = "${var.project}-nfl-feature-engineering"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.feature_engineering_task_cpu["nfl"])
  memory                   = tostring(local.feature_engineering_task_memory["nfl"])
  execution_role_arn       = aws_iam_role.ecs_pipeline.arn
  task_role_arn            = aws_iam_role.ecs_pipeline.arn

  container_definitions = jsonencode([
    {
      name      = "nfl-feature-engineering"
      image     = "${var.ecr_repo_url}:nfl-feature-engineering-latest"
      essential = true
      environment = [
        # FeatureStorage's constructor requires all four table names
        # unconditionally, even though build_dataset.py itself only reads
        # events/player_game_stats/team_game_stats.
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
          "awslogs-group"         = aws_cloudwatch_log_group.nfl_feature_engineering.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "feature-engineering"
        }
      }
    }
  ])

  tags = merge(local.common_tags, {
    Sport     = "nfl"
    Component = "training"
  })
}

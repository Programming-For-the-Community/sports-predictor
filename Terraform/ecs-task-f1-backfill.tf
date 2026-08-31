# 30-day retention so logs don't grow unbounded; a backfill run's logs are
# only useful for debugging a recent failure, not as a long-term record.
resource "aws_cloudwatch_log_group" "f1_backfill" {
  name              = "/ecs/${var.project}-f1-backfill"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "f1"
    Component = "ingestion"
  })
}

# Standalone Fargate task (launched via `aws ecs run-task`, not a Service --
# runs to completion and stops, no always-on cost). Pass
# --propagate-tags TASK_DEFINITION on that command or the running task
# won't carry these tags for cost allocation -- RunTask doesn't propagate
# them by default. Runs in a public subnet with a public IP to reach
# Jolpica-F1's public API.
#
# PLAYER_GAME_STATS_TABLE_NAME/TEAM_GAME_STATS_TABLE_NAME ARE still set
# below even though F1's PipelineStorage usage never touches either table
# (see iam-f1-backfill.tf's own header comment, which is why neither table
# gets an IAM grant here) -- PipelineStorage.__init__ requires both env
# vars unconditionally just to construct the (unused) DynamoDBTable
# wrappers, same reason ecs-task-f1-feature-engineering.tf sets all four
# FeatureStorage table vars regardless of which ones build_dataset.py
# actually reads.
#
# START_SEASON defaults to 2010 (not just "the usual ~10 years") --
# library/features/f1_points.py only implements F1's CURRENT points
# table, which took effect in 2010; see data-backfills/f1/backfill.py's
# own module docstring for the full reasoning. REQUEST_DELAY_SECONDS
# defaults to Jolpica's own stricter sustained-rate bound (7.2s/request,
# library/http/f1.py's DEFAULT_MIN_INTERVAL_SECONDS) -- override these
# per-run via ECS "Run Task" -> Container overrides -> Environment
# variables.
resource "aws_ecs_task_definition" "f1_backfill" {
  family                   = "${var.project}-f1-backfill"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "1024"
  memory                   = "2048"
  execution_role_arn       = aws_iam_role.f1_backfill.arn
  task_role_arn            = aws_iam_role.f1_backfill.arn

  container_definitions = jsonencode([
    {
      name      = "f1-backfill"
      image     = "${var.ecr_repo_url}:f1-backfill-latest"
      essential = true
      environment = [
        { name = "RAW_BUCKET_NAME", value = aws_s3_bucket.raw_data_lake.bucket },
        { name = "ENTITIES_TABLE_NAME", value = aws_dynamodb_table.entities.name },
        { name = "EVENTS_TABLE_NAME", value = aws_dynamodb_table.events.name },
        { name = "PLAYER_GAME_STATS_TABLE_NAME", value = aws_dynamodb_table.player_game_stats.name },
        { name = "TEAM_GAME_STATS_TABLE_NAME", value = aws_dynamodb_table.team_game_stats.name },
        { name = "AWS_REGION", value = var.region },
        { name = "JOLPICA_API_ROOT_URL", value = var.jolpica_api_root_url },
        { name = "JOLPICA_USER_AGENT", value = var.jolpica_user_agent },
        { name = "START_SEASON", value = "2010" },
        { name = "END_SEASON", value = "2026" },
        { name = "BATCH_SIZE", value = "3" },
        { name = "REQUEST_DELAY_SECONDS", value = "7.2" },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.f1_backfill.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "backfill"
        }
      }
    }
  ])

  tags = merge(local.common_tags, {
    Sport     = "f1"
    Component = "ingestion"
  })
}

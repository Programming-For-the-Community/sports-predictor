# 30-day retention so logs don't grow unbounded; a backfill run's logs are
# only useful for debugging a recent failure, not as a long-term record.
resource "aws_cloudwatch_log_group" "ncaambb_backfill" {
  name              = "/ecs/${var.project}-ncaambb-backfill"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "ncaambb"
    Component = "ingestion"
  })
}

# Standalone Fargate task (launched via `aws ecs run-task`, not a Service --
# runs to completion and stops, no always-on cost). Pass
# --propagate-tags TASK_DEFINITION on that command or the running task
# won't carry these tags for cost allocation -- RunTask doesn't propagate
# them by default. Runs in a public subnet with a public IP to reach
# ESPN's public API.
#
# 2048 CPU / 4096 memory -- double NBA's own 1024/2048. D1's ~362 teams
# and up to ~150-155 games on a single date (confirmed live, 2026-08-19 --
# see project-ncaambb-onboarding memory) mean backfill.py's own
# ThreadPoolExecutor concurrency (both the season-batch level and the new
# per-date event level -- see backfill.py's own VOLUME docstring section)
# has real, simultaneous JSON parsing/normalization work to do, not just
# more time spent waiting on the shared rate limiter.
#
# START_SEASON/END_SEASON/BATCH_SIZE/REQUEST_DELAY_SECONDS default to a
# full historical run here; override them per-run via ECS "Run Task" ->
# Container overrides -> Environment variables.
resource "aws_ecs_task_definition" "ncaambb_backfill" {
  family                   = "${var.project}-ncaambb-backfill"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "2048"
  memory                   = "4096"
  execution_role_arn       = aws_iam_role.ncaambb_backfill.arn
  task_role_arn            = aws_iam_role.ncaambb_backfill.arn

  container_definitions = jsonencode([
    {
      name      = "ncaambb-backfill"
      image     = "${var.ecr_repo_url}:ncaambb-backfill-latest"
      essential = true
      environment = [
        { name = "RAW_BUCKET_NAME", value = aws_s3_bucket.raw_data_lake.bucket },
        { name = "ENTITIES_TABLE_NAME", value = aws_dynamodb_table.entities.name },
        { name = "EVENTS_TABLE_NAME", value = aws_dynamodb_table.events.name },
        { name = "PLAYER_GAME_STATS_TABLE_NAME", value = aws_dynamodb_table.player_game_stats.name },
        { name = "TEAM_GAME_STATS_TABLE_NAME", value = aws_dynamodb_table.team_game_stats.name },
        { name = "AWS_REGION", value = var.region },
        { name = "ESPN_API_ROOT_URL", value = var.espn_api_root_url },
        { name = "ESPN_USER_AGENT", value = var.espn_user_agent },
        { name = "START_SEASON", value = "2016" },
        { name = "END_SEASON", value = "2026" },
        { name = "BATCH_SIZE", value = "2" },
        { name = "REQUEST_DELAY_SECONDS", value = "0.3" },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.ncaambb_backfill.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "backfill"
        }
      }
    }
  ])

  tags = merge(local.common_tags, {
    Sport     = "ncaambb"
    Component = "ingestion"
  })
}

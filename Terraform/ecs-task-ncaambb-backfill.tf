resource "aws_cloudwatch_log_group" "ncaambb_backfill" {
  name              = "/ecs/${var.project}-ncaambb-backfill"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "ncaambb"
    Component = "ingestion"
  })
}

# Launch via sfn-backfill-orchestrator.tf, not `aws ecs run-task` directly.
# Public subnet/IP to reach ESPN's public API.
#
# 2048 CPU / 4096 memory -- double NBA's, for D1's ~362 teams / ~150-155
# games per date (backfill.py's ThreadPoolExecutor concurrency).
#
# START_SEASON/END_SEASON/BATCH_SIZE/REQUEST_DELAY_SECONDS default to a
# full historical run; override via the orchestrator's container_overrides.
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
        { name = "NCAAMBB_ESPN_CORE_API_ROOT_URL", value = var.ncaambb_espn_core_api_root_url },
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

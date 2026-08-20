# 30-day retention so logs don't grow unbounded.
resource "aws_cloudwatch_log_group" "ncaafb_backfill" {
  name              = "/ecs/${var.project}-ncaafb-backfill"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "ncaa-fb"
    Component = "ingestion"
  })
}

# Standalone Fargate task (launched via `aws ecs run-task`, not a Service).
# Pass --propagate-tags TASK_DEFINITION on that command or the running
# task won't carry these tags for cost allocation -- RunTask doesn't
# propagate them by default. Runs in a public subnet
# (fargate_internet_egress) to reach CFBD's public API.
#
# Uses the dedicated CFBD_API_KEY_SECRET_FIELD "ncaa_fb_backfill_key",
# separate from ingest/schedule-sync's "ncaa_fb_ingest_key", so a
# long-running backfill never competes with production ingest for the
# same CFBD free-tier call budget.
#
# START_SEASON/END_SEASON default to a full historical run; override
# per-run via ECS "Run Task" -> Container overrides.
resource "aws_ecs_task_definition" "ncaafb_backfill" {
  family                   = "${var.project}-ncaafb-backfill"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "1024"
  memory                   = "2048"
  execution_role_arn       = aws_iam_role.ncaafb_backfill.arn
  task_role_arn            = aws_iam_role.ncaafb_backfill.arn

  container_definitions = jsonencode([
    {
      name      = "ncaafb-backfill"
      image     = "${var.ecr_repo_url}:ncaafb-backfill-latest"
      essential = true
      environment = [
        { name = "RAW_BUCKET_NAME", value = aws_s3_bucket.raw_data_lake.bucket },
        { name = "ENTITIES_TABLE_NAME", value = aws_dynamodb_table.entities.name },
        { name = "EVENTS_TABLE_NAME", value = aws_dynamodb_table.events.name },
        { name = "PLAYER_GAME_STATS_TABLE_NAME", value = aws_dynamodb_table.player_game_stats.name },
        { name = "TEAM_GAME_STATS_TABLE_NAME", value = aws_dynamodb_table.team_game_stats.name },
        { name = "AWS_REGION", value = var.region },
        { name = "CFBD_API_ROOT_URL", value = var.cfbd_api_root_url },
        { name = "CFBD_API_KEY_SECRET_ARN", value = var.third_party_api_key_secret_arn },
        { name = "CFBD_API_KEY_SECRET_FIELD", value = "ncaa_fb_backfill_key" },
        { name = "START_SEASON", value = "2015" },
        { name = "END_SEASON", value = "2025" },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.ncaafb_backfill.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "backfill"
        }
      }
    }
  ])

  tags = merge(local.common_tags, {
    Sport     = "ncaa-fb"
    Component = "ingestion"
  })
}

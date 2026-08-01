# 30-day retention so logs don't grow unbounded (see design/PROJECT_PLAN.md
# Phase 7) -- a backfill run's logs are only useful for debugging a recent
# failure, not as a long-term record (that's what S3's raw landing zone
# and DynamoDB are for).
resource "aws_cloudwatch_log_group" "nfl_backfill" {
  name              = "/ecs/${var.project}-nfl-backfill"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "nfl"
    Component = "ingestion"
  })
}

# Standalone Fargate task (launched via `aws ecs run-task`, not a Service
# -- this runs to completion and stops, no always-on cost). Runs in a
# public subnet with a public IP rather than a private subnet + NAT
# Gateway, since it needs to reach ESPN's public API and a NAT Gateway
# alone (~$32/month) exceeds this project's $15/month budget. See
# aws_security_group.fargate_internet_egress in security-groups.tf.
#
# START_SEASON/END_SEASON/BATCH_SIZE/REQUEST_DELAY_SECONDS default to a
# full historical run here; override them per-run via ECS "Run Task" ->
# Container overrides -> Environment variables to backfill a narrower
# range (e.g. retrying one failed season) without touching this
# definition. See Source/data-backfills/nfl/backfill.py's docstring.
resource "aws_ecs_task_definition" "nfl_backfill" {
  family                   = "${var.project}-nfl-backfill"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "1024"
  memory                   = "2048"
  execution_role_arn       = aws_iam_role.nfl_backfill.arn
  task_role_arn            = aws_iam_role.nfl_backfill.arn

  container_definitions = jsonencode([
    {
      name      = "nfl-backfill"
      image     = "${var.ecr_repo_url}:nfl-backfill-latest"
      essential = true
      environment = [
        { name = "RAW_BUCKET_NAME", value = aws_s3_bucket.raw_data_lake.bucket },
        { name = "ENTITIES_TABLE_NAME", value = aws_dynamodb_table.entities.name },
        { name = "EVENTS_TABLE_NAME", value = aws_dynamodb_table.events.name },
        { name = "PLAYER_GAME_STATS_TABLE_NAME", value = aws_dynamodb_table.player_game_stats.name },
        { name = "TEAM_GAME_STATS_TABLE_NAME", value = aws_dynamodb_table.team_game_stats.name },
        { name = "AWS_REGION", value = var.region },
        { name = "ESPN_API_ROOT_URL", value = var.espn_api_root_url },
        { name = "START_SEASON", value = "2016" },
        { name = "END_SEASON", value = "2025" },
        { name = "BATCH_SIZE", value = "2" },
        { name = "REQUEST_DELAY_SECONDS", value = "0.3" },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.nfl_backfill.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "backfill"
        }
      }
    }
  ])

  tags = merge(local.common_tags, {
    Sport     = "nfl"
    Component = "ingestion"
  })
}

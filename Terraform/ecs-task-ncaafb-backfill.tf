# 30-day retention, same rationale as ecs-task-nfl-backfill.tf.
resource "aws_cloudwatch_log_group" "ncaafb_backfill" {
  name              = "/ecs/${var.project}-ncaafb-backfill"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "ncaa-fb"
    Component = "ingestion"
  })
}

# Standalone Fargate task (launched via `aws ecs run-task`, not a Service or
# Step Functions -- unlike the feature-engineering/train-* tasks, whose
# ecs:runTask.sync calls in sfn-training-orchestrator.tf already set
# "PropagateTags": "TASK_DEFINITION"). Pass --propagate-tags TASK_DEFINITION
# on that command yourself (or the equivalent console option) or the
# running task won't carry this file's tags for cost allocation -- RunTask
# doesn't propagate them by default. Same gotcha ecs-task-nfl-backfill.tf's
# own comment documents, and the one that actually bit NFL's backfill task
# the first time it was run manually without the flag. Runs in a public
# subnet (fargate_internet_egress) to reach CFBD's public API, same
# NAT-Gateway-cost reasoning.
#
# Uses the dedicated CFBD_API_KEY_SECRET_FIELD "ncaa_fb_backfill_key" --
# deliberately a different key than ingest/schedule-sync's
# "ncaa_fb_ingest_key" (see project memory's Phase 0 notes), so a month-long
# backfill run never competes with production ingest for the same
# 1,000-call/month CFBD free-tier budget.
#
# START_SEASON/END_SEASON default to a full historical run; override per-run
# via ECS "Run Task" -> Container overrides, same as
# ecs-task-nfl-backfill.tf's own pattern.
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

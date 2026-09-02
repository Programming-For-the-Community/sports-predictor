# 30-day retention so logs don't grow unbounded; a backfill run's logs are
# only useful for debugging a recent failure, not as a long-term record.
resource "aws_cloudwatch_log_group" "pga_backfill" {
  name              = "/ecs/${var.project}-pga-backfill"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "pga"
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
# PLAYER_GAME_STATS_TABLE_NAME/TEAM_GAME_STATS_TABLE_NAME are set below
# even though PGA's PipelineStorage usage never touches either table
# (see iam-pga-backfill.tf's own header comment, which is why neither
# table gets an IAM grant here) -- PipelineStorage.__init__ requires both
# env vars unconditionally just to construct the DynamoDBTable wrappers.
#
# START_SEASON/END_SEASON/BATCH_SIZE/REQUEST_DELAY_SECONDS default to a
# full historical run here; override them per-run via ECS "Run Task" ->
# Container overrides -> Environment variables.
resource "aws_ecs_task_definition" "pga_backfill" {
  family                   = "${var.project}-pga-backfill"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "1024"
  memory                   = "2048"
  execution_role_arn       = aws_iam_role.pga_backfill.arn
  task_role_arn            = aws_iam_role.pga_backfill.arn

  container_definitions = jsonencode([
    {
      name      = "pga-backfill"
      image     = "${var.ecr_repo_url}:pga-backfill-latest"
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
        { name = "START_SEASON", value = "2017" },
        { name = "END_SEASON", value = "2026" },
        { name = "BATCH_SIZE", value = "3" },
        { name = "REQUEST_DELAY_SECONDS", value = "0.3" },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.pga_backfill.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "backfill"
        }
      }
    }
  ])

  tags = merge(local.common_tags, {
    Sport     = "pga"
    Component = "ingestion"
  })
}

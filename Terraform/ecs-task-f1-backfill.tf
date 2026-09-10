resource "aws_cloudwatch_log_group" "f1_backfill" {
  name              = "/ecs/${var.project}-f1-backfill"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "f1"
    Component = "ingestion"
  })
}

# Launch via sfn-backfill-orchestrator.tf, not `aws ecs run-task` directly.
# Public subnet/IP to reach Jolpica-F1's public API.
#
# PLAYER_GAME_STATS_TABLE_NAME/TEAM_GAME_STATS_TABLE_NAME are set even
# though F1 never uses either table -- PipelineStorage.__init__ requires
# both env vars unconditionally.
#
# START_SEASON defaults to 2010 -- library/features/f1_points.py only
# implements the points table that took effect that year. REQUEST_DELAY_SECONDS
# defaults to Jolpica's sustained-rate bound (library/http/f1.py's
# DEFAULT_MIN_INTERVAL_SECONDS). Override via the orchestrator's
# container_overrides.
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

resource "aws_cloudwatch_log_group" "pga_backfill" {
  name              = "/ecs/${var.project}-pga-backfill"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "pga"
    Component = "ingestion"
  })
}

# Launch via sfn-backfill-orchestrator.tf, not `aws ecs run-task` directly.
# Public subnet/IP to reach ESPN's public API.
#
# PLAYER_GAME_STATS_TABLE_NAME/TEAM_GAME_STATS_TABLE_NAME are set even
# though PGA never uses either table -- PipelineStorage.__init__ requires
# both env vars unconditionally.
#
# START_SEASON/END_SEASON/BATCH_SIZE/REQUEST_DELAY_SECONDS default to a
# full historical run; override via the orchestrator's container_overrides.
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

# 30-day retention, same rationale as ecs-task-nfl-backfill.tf.
resource "aws_cloudwatch_log_group" "nfl_feature_engineering" {
  name              = "/ecs/${var.project}-nfl-feature-engineering"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "nfl"
    Component = "training"
  })
}

# Standalone Fargate task (launched via `aws ecs run-task`, not a Service --
# runs to completion and stops, same pattern as ecs-task-nfl-backfill.tf).
# Reads the full events/player_game_stats history and writes two training
# Parquet files to the model artifacts bucket -- see
# Source/feature-engineering/nfl/build_dataset.py.
#
# cpu/memory bumped from 512/1024 after a real run against ~2,700 events
# and ~158,000 player-game rows hit an OutOfMemoryError (exit 137) at
# 1024MB. The proximate cause was a memory-inefficient Parquet-writing
# step (fixed in build_dataset.py -- it was holding a full second copy of
# the entire player-rows dataset alongside the original list, the
# DataFrame, and pyarrow's own Table, all at once, just to JSON-encode one
# column). This sizing is a safety margin on top of that fix, not a
# substitute for it -- matches backfill's sizing, and costs a fraction of
# a cent more per run either way.
#
# Uses the shared aws_iam_role.ecs_pipeline (iam-ecs-pipeline.tf) rather
# than a dedicated role like nfl_backfill's -- that role was already scoped
# for exactly this: read the three data tables, write model artifacts,
# shared with the future Train Model task too.
#
# Unlike backfill, this task never calls a third-party API -- it only
# touches DynamoDB and S3, both reachable from a private subnet via the
# Gateway Endpoints in vpc-endpoints.tf. But launch it in the SAME public
# subnet + fargate_internet_egress security group backfill uses anyway
# (set at `run-task` time, not here -- see security-groups.tf), not the
# more tightly-scoped ecs_pipeline security group its own comment
# describes: that comment's private-subnet design needs ECR VPC Interface
# Endpoints (com.amazonaws.<region>.ecr.api/.ecr.dkr) to pull this task's
# image, and those aren't free (~$7.30/month each) unlike the Gateway
# Endpoints. Reusing backfill's already-proven, free public-subnet path
# avoids that cost until/unless private-subnet Fargate becomes worth
# paying for across enough tasks to justify it.
resource "aws_ecs_task_definition" "nfl_feature_engineering" {
  family                   = "${var.project}-nfl-feature-engineering"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "1024"
  memory                   = "2048"
  execution_role_arn       = aws_iam_role.ecs_pipeline.arn
  task_role_arn            = aws_iam_role.ecs_pipeline.arn

  container_definitions = jsonencode([
    {
      name      = "nfl-feature-engineering"
      image     = "${var.ecr_repo_url}:nfl-feature-engineering-latest"
      essential = true
      environment = [
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

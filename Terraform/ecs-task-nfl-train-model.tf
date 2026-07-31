# 30-day retention, same rationale as ecs-task-nfl-backfill.tf.
resource "aws_cloudwatch_log_group" "nfl_train_model" {
  name              = "/ecs/${var.project}-nfl-train-model"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "nfl"
    Component = "training"
  })
}

# Standalone Fargate task (launched via `aws ecs run-task`, not a Service --
# runs to completion and stops, same pattern as
# ecs-task-nfl-feature-engineering.tf). Reads event_features.parquet,
# trains the NFL win-probability model, and writes a versioned artifact
# plus metadata to the model artifacts bucket -- see
# Source/model-training/nfl/train_model.py.
#
# Scheduled -- see scheduler-nfl-train-model.tf -- but every bit as
# runnable manually via `aws ecs run-task` if you want to retrain and
# inspect a specific run's output without waiting for Wednesday.
#
# Uses the shared aws_iam_role.ecs_pipeline (iam-ecs-pipeline.tf), same as
# feature engineering -- it's already scoped for both. cpu/memory match
# feature engineering's sizing (1024/2048) as a starting point even though
# training on ~2,700 rows should need far less; XGBoost fit time at this
# scale is seconds, not the bottleneck. Cheap to right-size down later
# once a real run shows actual usage.
resource "aws_ecs_task_definition" "nfl_train_model" {
  family                   = "${var.project}-nfl-train-model"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "1024"
  memory                   = "2048"
  execution_role_arn       = aws_iam_role.ecs_pipeline.arn
  task_role_arn            = aws_iam_role.ecs_pipeline.arn

  container_definitions = jsonencode([
    {
      name      = "nfl-train-model"
      image     = "${var.ecr_repo_url}:nfl-train-model-latest"
      essential = true
      environment = [
        { name = "MODEL_ARTIFACTS_BUCKET_NAME", value = aws_s3_bucket.model_artifacts.bucket },
        { name = "AWS_REGION", value = var.region },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.nfl_train_model.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "train-model"
        }
      }
    }
  ])

  tags = merge(local.common_tags, {
    Sport     = "nfl"
    Component = "training"
  })
}

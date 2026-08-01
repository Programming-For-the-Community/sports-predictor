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
# feature engineering. cpu=2048 (2 vCPU) gives _tune_hyperparameters'
# n_jobs=-1 RandomizedSearchCV actual cores to spread its (candidate,
# fold) fits across, rather than running all of them sequentially on a
# single core. memory=4096 is Fargate's minimum allowed at 2 vCPU, well
# above what this workload's ~2,700-row dataset needs on its own.
resource "aws_ecs_task_definition" "nfl_train_model" {
  family                   = "${var.project}-nfl-train-model"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "2048"
  memory                   = "4096"
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

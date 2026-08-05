# 30-day retention, same rationale as ecs-task-nfl-backfill.tf.
resource "aws_cloudwatch_log_group" "nfl_train_win_probability_model" {
  name              = "/ecs/${var.project}-nfl-train-win-probability-model"
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
# Source/model-training/nfl/train_win_probability_model.py. Named to match
# train-score-model/train-player-prop-model's convention of naming the
# task/image after what it actually trains, rather than the generic
# "train-model" this originally shipped as (the only one of the three not
# already following that pattern).
#
# Scheduled -- see scheduler-nfl-train-win-probability-model.tf -- but
# every bit as runnable manually via `aws ecs run-task` if you want to
# retrain and inspect a specific run's output without waiting for
# Wednesday.
#
# Uses the shared aws_iam_role.ecs_pipeline (iam-ecs-pipeline.tf), same as
# feature engineering. cpu=4096 (4 vCPU) gives _tune_hyperparameters'
# n_jobs=-1 RandomizedSearchCV actual cores to spread its ~2,400
# (candidate, fold) fits across -- confirmed CPU-bound, not I/O-bound, by
# CloudWatch Container Insights showing a 2 vCPU allocation pegged near
# 100% utilization throughout the search, so more cores translate almost
# directly into a shorter run. Stepped down from an earlier 8 vCPU after
# hitting the account's Fargate on-demand vCPU quota trying to launch
# every NFL training task at once -- see scheduler-nfl-train-win-probability-model.tf's
# 30-minute stagger, the other half of that fix. memory=16384 is well
# within Fargate's valid range at 4 vCPU (8-30GB) and well above what
# this workload's ~2,700-row dataset needs on its own.
resource "aws_ecs_task_definition" "nfl_train_win_probability_model" {
  family                   = "${var.project}-nfl-train-win-probability-model"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "4096"
  memory                   = "16384"
  execution_role_arn       = aws_iam_role.ecs_pipeline.arn
  task_role_arn            = aws_iam_role.ecs_pipeline.arn

  container_definitions = jsonencode([
    {
      name      = "nfl-train-win-probability-model"
      image     = "${var.ecr_repo_url}:nfl-train-win-probability-model-latest"
      essential = true
      environment = [
        { name = "MODEL_ARTIFACTS_BUCKET_NAME", value = aws_s3_bucket.model_artifacts.bucket },
        { name = "AWS_REGION", value = var.region },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.nfl_train_win_probability_model.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "train-win-probability-model"
        }
      }
    }
  ])

  tags = merge(local.common_tags, {
    Sport     = "nfl"
    Component = "training"
  })
}

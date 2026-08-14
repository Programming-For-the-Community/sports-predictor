# 30-day retention, same rationale as ecs-task-nfl-backfill.tf.
resource "aws_cloudwatch_log_group" "nba_train_win_probability_model" {
  name              = "/ecs/${var.project}-nba-train-win-probability-model"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "nba"
    Component = "training"
  })
}

# Standalone Fargate task, same shape as
# ecs-task-ncaafb-train-win-probability-model.tf. Reads event_features.parquet,
# trains the NBA win-probability model against the existing training
# harness's hyperparameter search (now including the LightGBM candidate
# family added for basketball's data volume -- see
# library/ml/model_types.py), and writes a versioned artifact to the model
# artifacts bucket -- see
# Source/model-training/nba/train_win_probability_model.py.
#
# Scheduled via the registry-driven training orchestrator
# (sfn-training-orchestrator.tf, dynamodb-sport-registry.tf's nba_registry
# item) -- no per-sport scheduler file needed. cpu/memory come from
# locals-training-compute.tf, same shared budget every sport's training
# tasks draw from.
resource "aws_ecs_task_definition" "nba_train_win_probability_model" {
  family                   = "${var.project}-nba-train-win-probability-model"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = local.training_task_cpu
  memory                   = local.training_task_memory
  execution_role_arn       = aws_iam_role.ecs_pipeline.arn
  task_role_arn            = aws_iam_role.ecs_pipeline.arn

  container_definitions = jsonencode([
    {
      name      = "nba-train-win-probability-model"
      image     = "${var.ecr_repo_url}:nba-train-win-probability-model-latest"
      essential = true
      environment = [
        { name = "MODEL_ARTIFACTS_BUCKET_NAME", value = aws_s3_bucket.model_artifacts.bucket },
        { name = "AWS_REGION", value = var.region },
        # Same BLAS-oversubscription guard as
        # ecs-task-nfl-train-win-probability-model.tf's own comment.
        { name = "OMP_NUM_THREADS", value = "1" },
        { name = "OPENBLAS_NUM_THREADS", value = "1" },
        { name = "MKL_NUM_THREADS", value = "1" },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.nba_train_win_probability_model.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "train-win-probability-model"
        }
      }
    }
  ])

  tags = merge(local.common_tags, {
    Sport     = "nba"
    Component = "training"
  })
}

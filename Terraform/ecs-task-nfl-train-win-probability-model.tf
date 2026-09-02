# 30-day log retention.
resource "aws_cloudwatch_log_group" "nfl_train_win_probability_model" {
  name              = "/ecs/${var.project}-nfl-train-win-probability-model"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "nfl"
    Component = "training"
  })
}

# Standalone Fargate task (run via `aws ecs run-task`, not a Service). Reads
# event_features.parquet, trains the NFL win-probability model, and writes
# a versioned artifact plus metadata to the model artifacts bucket -- see
# Source/model-training/nfl/train_win_probability_model.py.
#
# Run by the training orchestrator's registry-driven Distributed Map
# (sfn-training-orchestrator.tf) -- also runnable manually via
# `aws ecs run-task`.
#
# Uses the shared aws_iam_role.ecs_pipeline (iam-ecs-pipeline.tf). cpu/memory
# come from locals-training-compute.tf (var.training_task_vcpu /
# var.training_task_memory_per_vcpu_mib) -- every model_types.py adapter's
# outer search (RandomizedSearchCV/GridSearchCV) sets n_jobs=-1, so this
# task's vCPU count is how many (candidate, fold) fits it can run at once.
resource "aws_ecs_task_definition" "nfl_train_win_probability_model" {
  family                   = "${var.project}-nfl-train-win-probability-model"
  requires_compatibilities = ["EC2"]
  network_mode             = "awsvpc"
  cpu                      = local.training_task_cpu
  memory                   = local.training_task_memory
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
        # BLAS-oversubscription guard: caps thread pools so scikit-learn
        # doesn't over-parallelize on a bounded-vCPU Fargate task -- same
        # guard every other sport's own train-* task definition sets.
        { name = "OMP_NUM_THREADS", value = "1" },
        { name = "OPENBLAS_NUM_THREADS", value = "1" },
        { name = "MKL_NUM_THREADS", value = "1" },
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

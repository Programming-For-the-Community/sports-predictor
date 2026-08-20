# 30-day log retention.
resource "aws_cloudwatch_log_group" "nfl_train_score_model" {
  name              = "/ecs/${var.project}-nfl-train-score-model"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "nfl"
    Component = "training"
  })
}

# Standalone Fargate task, one definition shared by all three score targets
# (margin, home_score, away_score) -- see scheduler-nfl-train-score-model.tf,
# which schedules it once per target in nfl_score_targets via a
# per-schedule SCORE_TARGET override. Runnable manually via
# `aws ecs run-task` with your own override.
#
# All four training scripts live in one Dockerfile (Source/model-training/nfl/Dockerfile);
# this overrides the container command to run train_score_model.py. Reads
# the same event_features.parquet as the win-probability task.
#
# Does not set SCORE_TARGET here -- it varies per run, so it's passed as a
# `containerOverrides[].environment` override on each invocation instead
# of baked into the task definition. train_score_model.py reads it via
# os.environ["SCORE_TARGET"], raising KeyError if it's missing.
#
# Uses the shared aws_iam_role.ecs_pipeline (iam-ecs-pipeline.tf).
resource "aws_ecs_task_definition" "nfl_train_score_model" {
  family                   = "${var.project}-nfl-train-score-model"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = local.training_task_cpu
  memory                   = local.training_task_memory
  execution_role_arn       = aws_iam_role.ecs_pipeline.arn
  task_role_arn            = aws_iam_role.ecs_pipeline.arn

  container_definitions = jsonencode([
    {
      name      = "nfl-train-score-model"
      image     = "${var.ecr_repo_url}:nfl-train-win-probability-model-latest"
      command   = ["train_score_model.py"]
      essential = true
      environment = [
        { name = "MODEL_ARTIFACTS_BUCKET_NAME", value = aws_s3_bucket.model_artifacts.bucket },
        { name = "AWS_REGION", value = var.region },
        # Avoids BLAS oversubscribing against the outer search's own
        # n_jobs=-1 process-level parallelism.
        { name = "OMP_NUM_THREADS", value = "1" },
        { name = "OPENBLAS_NUM_THREADS", value = "1" },
        { name = "MKL_NUM_THREADS", value = "1" },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.nfl_train_score_model.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "train-score-model"
        }
      }
    }
  ])

  tags = merge(local.common_tags, {
    Sport     = "nfl"
    Component = "training"
  })
}

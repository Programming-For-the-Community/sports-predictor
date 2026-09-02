# 30-day retention so logs don't grow unbounded.
resource "aws_cloudwatch_log_group" "ncaambb_train_score_model" {
  name              = "/ecs/${var.project}-ncaambb-train-score-model"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "ncaambb"
    Component = "training"
  })
}

# Standalone Fargate task, one definition shared by all three NCAA MBB
# score targets (margin, home_score, away_score). The training
# orchestrator's registry item (dynamodb-sport-registry.tf's
# ncaambb_registry) schedules it once per target via a per-invocation
# SCORE_TARGET container override, not set here.
#
# Reuses the win-probability task's own image (all NCAA MBB training
# scripts live in one Dockerfile) and overrides the container command to
# run train_score_model.py.
resource "aws_ecs_task_definition" "ncaambb_train_score_model" {
  family                   = "${var.project}-ncaambb-train-score-model"
  requires_compatibilities = ["EC2"]
  network_mode             = "awsvpc"
  cpu                      = local.training_task_cpu
  memory                   = local.training_task_memory
  execution_role_arn       = aws_iam_role.ecs_pipeline.arn
  task_role_arn            = aws_iam_role.ecs_pipeline.arn

  container_definitions = jsonencode([
    {
      name      = "ncaambb-train-score-model"
      image     = "${var.ecr_repo_url}:ncaambb-train-win-probability-model-latest"
      command   = ["train_score_model.py"]
      essential = true
      environment = [
        { name = "MODEL_ARTIFACTS_BUCKET_NAME", value = aws_s3_bucket.model_artifacts.bucket },
        { name = "AWS_REGION", value = var.region },
        { name = "OMP_NUM_THREADS", value = "1" },
        { name = "OPENBLAS_NUM_THREADS", value = "1" },
        { name = "MKL_NUM_THREADS", value = "1" },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.ncaambb_train_score_model.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "train-score-model"
        }
      }
    }
  ])

  tags = merge(local.common_tags, {
    Sport     = "ncaambb"
    Component = "training"
  })
}

# 30-day retention so logs don't grow unbounded.
resource "aws_cloudwatch_log_group" "f1_train_constructor_winprob_model" {
  name              = "/ecs/${var.project}-f1-train-constructor-winprob-model"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "f1"
    Component = "training"
  })
}

# Standalone Fargate task. Reuses the train-winprob-model task's own image
# (all nine F1 training scripts live in one Dockerfile -- model-training/
# f1/Dockerfile) and overrides the container command to run
# train_constructor_winprob_model.py -- the one F1 training script that
# reads a genuinely different dataset (constructor_features.parquet, not
# driver_features.parquet), but still ships in the same shared image,
# same shared-image/command-override pattern PGA's own train-top5-model
# task definition uses.
resource "aws_ecs_task_definition" "f1_train_constructor_winprob_model" {
  family                   = "${var.project}-f1-train-constructor-winprob-model"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = local.training_task_cpu
  memory                   = local.training_task_memory
  execution_role_arn       = aws_iam_role.ecs_pipeline.arn
  task_role_arn            = aws_iam_role.ecs_pipeline.arn

  container_definitions = jsonencode([
    {
      name      = "f1-train-constructor-winprob-model"
      image     = "${var.ecr_repo_url}:f1-train-winprob-model-latest"
      command   = ["train_constructor_winprob_model.py"]
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
          "awslogs-group"         = aws_cloudwatch_log_group.f1_train_constructor_winprob_model.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "train-constructor-winprob-model"
        }
      }
    }
  ])

  tags = merge(local.common_tags, {
    Sport     = "f1"
    Component = "training"
  })
}

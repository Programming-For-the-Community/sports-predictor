# 30-day retention so logs don't grow unbounded.
resource "aws_cloudwatch_log_group" "f1_train_sprint_grid_model" {
  name              = "/ecs/${var.project}-f1-train-sprint-grid-model"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "f1"
    Component = "training"
  })
}

# Standalone Fargate task. Reuses the train-winprob-model task's own image
# (all nine F1 training scripts live in one Dockerfile -- model-training/
# f1/Dockerfile) and overrides the container command to run
# train_sprint_grid_model.py, same shared-image/command-override pattern
# PGA's own train-top5-model task definition uses. Reads sprint_features.
# parquet -- by far the smallest of every F1 dataset (Sprint format only
# exists 2021+, and only a handful of rounds per season are Sprint
# weekends), see that script's own docstring.
resource "aws_ecs_task_definition" "f1_train_sprint_grid_model" {
  family                   = "${var.project}-f1-train-sprint-grid-model"
  requires_compatibilities = ["EC2"] # training is EC2-only now (sfn-training-orchestrator.tf); Fargate training was retired
  network_mode             = "awsvpc"
  cpu                      = local.training_task_cpu
  memory                   = local.training_task_memory
  execution_role_arn       = aws_iam_role.ecs_pipeline.arn
  task_role_arn            = aws_iam_role.ecs_pipeline.arn

  container_definitions = jsonencode([
    {
      name      = "f1-train-sprint-grid-model"
      image     = "${var.ecr_repo_url}:f1-train-winprob-model-latest"
      command   = ["train_sprint_grid_model.py"]
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
          "awslogs-group"         = aws_cloudwatch_log_group.f1_train_sprint_grid_model.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "train-sprint-grid-model"
        }
      }
    }
  ])

  tags = merge(local.common_tags, {
    Sport     = "f1"
    Component = "training"
  })
}

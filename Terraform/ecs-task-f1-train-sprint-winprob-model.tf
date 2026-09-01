# 30-day retention so logs don't grow unbounded.
resource "aws_cloudwatch_log_group" "f1_train_sprint_winprob_model" {
  name              = "/ecs/${var.project}-f1-train-sprint-winprob-model"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "f1"
    Component = "training"
  })
}

# Standalone Fargate task. Reuses the train-winprob-model task's own image
# (all nine F1 training scripts live in one Dockerfile -- model-training/
# f1/Dockerfile) and overrides the container command to run
# train_sprint_winprob_model.py, same shared-image/command-override
# pattern PGA's own train-top5-model task definition uses. Reads sprint_
# features.parquet -- by far the smallest of every F1 dataset, see
# train-sprint-grid-model's own comment.
resource "aws_ecs_task_definition" "f1_train_sprint_winprob_model" {
  family                   = "${var.project}-f1-train-sprint-winprob-model"
  requires_compatibilities = ["FARGATE", "EC2"] # EC2 for the parallel training track (sfn-training-orchestrator-ec2.tf); unchanged for Fargate
  network_mode             = "awsvpc"
  cpu                      = local.training_task_cpu
  memory                   = local.training_task_memory
  execution_role_arn       = aws_iam_role.ecs_pipeline.arn
  task_role_arn            = aws_iam_role.ecs_pipeline.arn

  container_definitions = jsonencode([
    {
      name      = "f1-train-sprint-winprob-model"
      image     = "${var.ecr_repo_url}:f1-train-winprob-model-latest"
      command   = ["train_sprint_winprob_model.py"]
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
          "awslogs-group"         = aws_cloudwatch_log_group.f1_train_sprint_winprob_model.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "train-sprint-winprob-model"
        }
      }
    }
  ])

  tags = merge(local.common_tags, {
    Sport     = "f1"
    Component = "training"
  })
}

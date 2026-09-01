# 30-day retention so logs don't grow unbounded.
resource "aws_cloudwatch_log_group" "pga_train_top5_model" {
  name              = "/ecs/${var.project}-pga-train-top5-model"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "pga"
    Component = "training"
  })
}

# Standalone Fargate task. Reuses the train-top10-model task's own image
# (all PGA training scripts live in one Dockerfile -- model-training/pga/
# Dockerfile) and overrides the container command to run
# train_top5_model.py, same shared-image/command-override pattern NBA's
# own train-score-model task definition uses.
resource "aws_ecs_task_definition" "pga_train_top5_model" {
  family                   = "${var.project}-pga-train-top5-model"
  requires_compatibilities = ["FARGATE", "EC2"] # EC2 for the parallel training track (sfn-training-orchestrator-ec2.tf); unchanged for Fargate
  network_mode             = "awsvpc"
  cpu                      = local.training_task_cpu
  memory                   = local.training_task_memory
  execution_role_arn       = aws_iam_role.ecs_pipeline.arn
  task_role_arn            = aws_iam_role.ecs_pipeline.arn

  container_definitions = jsonencode([
    {
      name      = "pga-train-top5-model"
      image     = "${var.ecr_repo_url}:pga-train-top10-model-latest"
      command   = ["train_top5_model.py"]
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
          "awslogs-group"         = aws_cloudwatch_log_group.pga_train_top5_model.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "train-top5-model"
        }
      }
    }
  ])

  tags = merge(local.common_tags, {
    Sport     = "pga"
    Component = "training"
  })
}

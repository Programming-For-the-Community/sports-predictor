# 30-day retention so logs don't grow unbounded.
resource "aws_cloudwatch_log_group" "pga_train_score_model" {
  name              = "/ecs/${var.project}-pga-train-score-model"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "pga"
    Component = "training"
  })
}

# Standalone Fargate task -- trains the projected-score-to-par regression
# model ("field finish order" is a serving-time ranking of this model's
# own predictions, not a separately trained artifact -- see docs/
# PGA_FEATURE_ENGINEERING.md and train_score_model.py's own docstring).
# Reuses the train-top10-model task's own image and overrides the
# container command, same shared-image/command-override pattern NBA's
# own train-score-model task definition uses.
resource "aws_ecs_task_definition" "pga_train_score_model" {
  family                   = "${var.project}-pga-train-score-model"
  requires_compatibilities = ["FARGATE", "EC2"] # EC2 for the parallel training track (sfn-training-orchestrator-ec2.tf); unchanged for Fargate
  network_mode             = "awsvpc"
  cpu                      = local.training_task_cpu
  memory                   = local.training_task_memory
  execution_role_arn       = aws_iam_role.ecs_pipeline.arn
  task_role_arn            = aws_iam_role.ecs_pipeline.arn

  container_definitions = jsonencode([
    {
      name      = "pga-train-score-model"
      image     = "${var.ecr_repo_url}:pga-train-top10-model-latest"
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
          "awslogs-group"         = aws_cloudwatch_log_group.pga_train_score_model.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "train-score-model"
        }
      }
    }
  ])

  tags = merge(local.common_tags, {
    Sport     = "pga"
    Component = "training"
  })
}

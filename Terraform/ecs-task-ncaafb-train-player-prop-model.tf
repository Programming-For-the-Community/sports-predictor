# 30-day retention so logs don't grow unbounded.
resource "aws_cloudwatch_log_group" "ncaafb_train_player_prop_model" {
  name              = "/ecs/${var.project}-ncaafb-train-player-prop-model"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "ncaafb"
    Component = "training"
  })
}

# Standalone Fargate task, one definition shared by every NCAAFB player-prop
# stat. The training orchestrator's registry item (dynamodb-sport-registry.tf's
# ncaafb_registry) schedules it once per stat via a per-invocation
# TARGET_STAT container override, not set here. Reuses the win-probability
# task's own image, overriding the container command to run
# train_player_prop_model.py.
resource "aws_ecs_task_definition" "ncaafb_train_player_prop_model" {
  family                   = "${var.project}-ncaafb-train-player-prop-model"
  requires_compatibilities = ["FARGATE", "EC2"] # EC2 for the parallel training track (sfn-training-orchestrator-ec2.tf); unchanged for Fargate
  network_mode             = "awsvpc"
  cpu                      = local.training_task_cpu
  memory                   = local.training_task_memory
  execution_role_arn       = aws_iam_role.ecs_pipeline.arn
  task_role_arn            = aws_iam_role.ecs_pipeline.arn

  container_definitions = jsonencode([
    {
      name      = "ncaafb-train-player-prop-model"
      image     = "${var.ecr_repo_url}:ncaafb-train-win-probability-model-latest"
      command   = ["train_player_prop_model.py"]
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
          "awslogs-group"         = aws_cloudwatch_log_group.ncaafb_train_player_prop_model.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "train-player-prop-model"
        }
      }
    }
  ])

  tags = merge(local.common_tags, {
    Sport     = "ncaafb"
    Component = "training"
  })
}

# 30-day retention, same rationale as ecs-task-nfl-backfill.tf.
resource "aws_cloudwatch_log_group" "ncaafb_train_score_model" {
  name              = "/ecs/${var.project}-ncaafb-train-score-model"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "ncaa-fb"
    Component = "training"
  })
}

# Standalone Fargate task, one definition shared by all three NCAAFB score
# targets (margin, home_score, away_score) -- same shape as
# ecs-task-nfl-train-score-model.tf. The training orchestrator's registry
# item (dynamodb-sport-registry.tf's ncaafb_registry) schedules it once per
# target via a per-invocation SCORE_TARGET container override; not set here,
# same reasoning as the NFL file.
#
# Reuses the win-probability task's own image (all four NCAAFB training
# scripts live in one Dockerfile, see Source/model-training/ncaafb/Dockerfile)
# and overrides the container command to run train_score_model.py.
resource "aws_ecs_task_definition" "ncaafb_train_score_model" {
  family                   = "${var.project}-ncaafb-train-score-model"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = local.training_task_cpu
  memory                   = local.training_task_memory
  execution_role_arn       = aws_iam_role.ecs_pipeline.arn
  task_role_arn            = aws_iam_role.ecs_pipeline.arn

  container_definitions = jsonencode([
    {
      name      = "ncaafb-train-score-model"
      image     = "${var.ecr_repo_url}:ncaafb-train-win-probability-model-latest"
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
          "awslogs-group"         = aws_cloudwatch_log_group.ncaafb_train_score_model.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "train-score-model"
        }
      }
    }
  ])

  tags = merge(local.common_tags, {
    Sport     = "ncaa-fb"
    Component = "training"
  })
}

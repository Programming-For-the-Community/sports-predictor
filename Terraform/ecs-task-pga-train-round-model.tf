# 30-day retention so logs don't grow unbounded.
resource "aws_cloudwatch_log_group" "pga_train_round_model" {
  name              = "/ecs/${var.project}-pga-train-round-model"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "pga"
    Component = "training"
  })
}

# Standalone Fargate task, one definition shared by all four PGA round
# targets (round 1-4) -- the training orchestrator's registry item
# (dynamodb-sport-registry.tf's pga_registry) schedules it once per round
# via a per-invocation ROUND_NUMBER container override, not set here (same
# SCORE_TARGET-style pattern NFL's own train-score-model task definition
# uses for margin/home_score/away_score).
#
# Reuses the train-top10-model task's own image and overrides the
# container command to run train_round_model.py.
resource "aws_ecs_task_definition" "pga_train_round_model" {
  family                   = "${var.project}-pga-train-round-model"
  requires_compatibilities = ["EC2"] # training is EC2-only now (sfn-training-orchestrator.tf); Fargate training was retired
  network_mode             = "awsvpc"
  cpu                      = local.training_task_cpu
  memory                   = local.training_task_memory
  execution_role_arn       = aws_iam_role.ecs_pipeline.arn
  task_role_arn            = aws_iam_role.ecs_pipeline.arn

  container_definitions = jsonencode([
    {
      name      = "pga-train-round-model"
      image     = "${var.ecr_repo_url}:pga-train-top10-model-latest"
      command   = ["train_round_model.py"]
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
          "awslogs-group"         = aws_cloudwatch_log_group.pga_train_round_model.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "train-round-model"
        }
      }
    }
  ])

  tags = merge(local.common_tags, {
    Sport     = "pga"
    Component = "training"
  })
}

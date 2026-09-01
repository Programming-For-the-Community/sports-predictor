# 30-day retention so logs don't grow unbounded.
resource "aws_cloudwatch_log_group" "nba_train_win_probability_model" {
  name              = "/ecs/${var.project}-nba-train-win-probability-model"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "nba"
    Component = "training"
  })
}

# Standalone Fargate task. Reads event_features.parquet, trains the NBA
# win-probability model against the training harness's hyperparameter
# search (including the LightGBM candidate family for basketball's data
# volume), and writes a versioned artifact to the model artifacts bucket.
#
# Scheduled via the registry-driven training orchestrator
# (sfn-training-orchestrator.tf, dynamodb-sport-registry.tf's nba_registry
# item) -- no per-sport scheduler file needed. cpu/memory come from
# locals-training-compute.tf.
resource "aws_ecs_task_definition" "nba_train_win_probability_model" {
  family                   = "${var.project}-nba-train-win-probability-model"
  requires_compatibilities = ["FARGATE", "EC2"] # EC2 for the parallel training track (sfn-training-orchestrator-ec2.tf); unchanged for Fargate
  network_mode             = "awsvpc"
  cpu                      = local.training_task_cpu
  memory                   = local.training_task_memory
  execution_role_arn       = aws_iam_role.ecs_pipeline.arn
  task_role_arn            = aws_iam_role.ecs_pipeline.arn

  container_definitions = jsonencode([
    {
      name      = "nba-train-win-probability-model"
      image     = "${var.ecr_repo_url}:nba-train-win-probability-model-latest"
      essential = true
      environment = [
        { name = "MODEL_ARTIFACTS_BUCKET_NAME", value = aws_s3_bucket.model_artifacts.bucket },
        { name = "AWS_REGION", value = var.region },
        # BLAS-oversubscription guard: caps thread pools so scikit-learn
        # doesn't over-parallelize on a bounded-vCPU Fargate task.
        { name = "OMP_NUM_THREADS", value = "1" },
        { name = "OPENBLAS_NUM_THREADS", value = "1" },
        { name = "MKL_NUM_THREADS", value = "1" },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.nba_train_win_probability_model.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "train-win-probability-model"
        }
      }
    }
  ])

  tags = merge(local.common_tags, {
    Sport     = "nba"
    Component = "training"
  })
}

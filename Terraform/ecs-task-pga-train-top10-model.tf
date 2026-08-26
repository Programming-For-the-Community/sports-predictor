# 30-day retention so logs don't grow unbounded.
resource "aws_cloudwatch_log_group" "pga_train_top10_model" {
  name              = "/ecs/${var.project}-pga-train-top10-model"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "pga"
    Component = "training"
  })
}

# Standalone Fargate task. Reads golfer_features.parquet, trains the
# top-10-finish-probability model (see docs/PGA_FEATURE_ENGINEERING.md
# for why this is the "ranking-style model" Phase 5 step 3 built, not a
# plain win/loss classifier or a genuine multinomial one) against the
# shared training harness's hyperparameter search, and writes a versioned
# artifact to the model artifacts bucket.
#
# Scheduled via the registry-driven training orchestrator
# (sfn-training-orchestrator.tf, dynamodb-sport-registry.tf's pga_registry
# item) -- no per-sport scheduler file needed. cpu/memory come from the
# same shared local.training_task_cpu/local.training_task_memory every
# other sport's own train-* task definitions use (locals-training-
# compute.tf) -- not a per-sport map the way feature-engineering's own
# sizing is, since every sport's training task shares one fixed budget.
resource "aws_ecs_task_definition" "pga_train_top10_model" {
  family                   = "${var.project}-pga-train-top10-model"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = local.training_task_cpu
  memory                   = local.training_task_memory
  execution_role_arn       = aws_iam_role.ecs_pipeline.arn
  task_role_arn            = aws_iam_role.ecs_pipeline.arn

  container_definitions = jsonencode([
    {
      name      = "pga-train-top10-model"
      image     = "${var.ecr_repo_url}:pga-train-top10-model-latest"
      essential = true
      environment = [
        { name = "MODEL_ARTIFACTS_BUCKET_NAME", value = aws_s3_bucket.model_artifacts.bucket },
        { name = "AWS_REGION", value = var.region },
        # BLAS-oversubscription guard: caps thread pools so scikit-learn
        # doesn't over-parallelize on a bounded-vCPU Fargate task -- same
        # guard every other sport's own train-* task definition sets.
        { name = "OMP_NUM_THREADS", value = "1" },
        { name = "OPENBLAS_NUM_THREADS", value = "1" },
        { name = "MKL_NUM_THREADS", value = "1" },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.pga_train_top10_model.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "train-top10-model"
        }
      }
    }
  ])

  tags = merge(local.common_tags, {
    Sport     = "pga"
    Component = "training"
  })
}

# 30-day retention so logs don't grow unbounded.
resource "aws_cloudwatch_log_group" "f1_train_winprob_model" {
  name              = "/ecs/${var.project}-f1-train-winprob-model"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "f1"
    Component = "training"
  })
}

# Standalone Fargate task. Reads driver_features.parquet, trains the
# race-win-probability model against the shared training harness's
# candidate tournament, and writes a versioned artifact to the model
# artifacts bucket. This is the PRIMARY image -- every other F1 training
# task definition (train-podium-model, train-dnf-model, train-finish-
# position-model, train-qualifying-model, train-sprint-*-model) reuses
# this same image and overrides `command` to run its own script, same
# shared-image/command-override pattern PGA's own train-top5-model task
# definition uses (model-training/f1/Dockerfile builds one image, nine
# entrypoint scripts). train-constructor-winprob-model is the one
# exception -- see that file's own comment for why it's a genuinely
# separate image.
#
# Scheduled via the registry-driven training orchestrator
# (sfn-training-orchestrator.tf, dynamodb-sport-registry.tf's f1_registry
# item) -- no per-sport scheduler file needed. cpu/memory come from the
# same shared local.training_task_cpu/local.training_task_memory every
# other sport's own train-* task definitions use (locals-training-
# compute.tf) -- not a per-sport map the way feature-engineering's own
# sizing is, since every sport's training task shares one fixed budget.
resource "aws_ecs_task_definition" "f1_train_winprob_model" {
  family                   = "${var.project}-f1-train-winprob-model"
  requires_compatibilities = ["EC2"]
  network_mode             = "awsvpc"
  cpu                      = local.training_task_cpu
  memory                   = local.training_task_memory
  execution_role_arn       = aws_iam_role.ecs_pipeline.arn
  task_role_arn            = aws_iam_role.ecs_pipeline.arn

  container_definitions = jsonencode([
    {
      name      = "f1-train-winprob-model"
      image     = "${var.ecr_repo_url}:f1-train-winprob-model-latest"
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
          "awslogs-group"         = aws_cloudwatch_log_group.f1_train_winprob_model.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "train-winprob-model"
        }
      }
    }
  ])

  tags = merge(local.common_tags, {
    Sport     = "f1"
    Component = "training"
  })
}

# 30-day log retention.
resource "aws_cloudwatch_log_group" "nfl_train_win_probability_model" {
  name              = "/ecs/${var.project}-nfl-train-win-probability-model"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "nfl"
    Component = "training"
  })
}

# Standalone Fargate task (run via `aws ecs run-task`, not a Service). Reads
# event_features.parquet, trains the NFL win-probability model, and writes
# a versioned artifact plus metadata to the model artifacts bucket -- see
# Source/model-training/nfl/train_win_probability_model.py.
#
# Scheduled -- see scheduler-nfl-train-win-probability-model.tf -- but
# also runnable manually via `aws ecs run-task`.
#
# Uses the shared aws_iam_role.ecs_pipeline (iam-ecs-pipeline.tf). cpu/memory
# come from locals-training-compute.tf (var.training_task_vcpu /
# var.training_task_memory_per_vcpu_mib) -- every model_types.py adapter's
# outer search (RandomizedSearchCV/GridSearchCV) sets n_jobs=-1, so this
# task's vCPU count is how many (candidate, fold) fits it can run at once.
resource "aws_ecs_task_definition" "nfl_train_win_probability_model" {
  family                   = "${var.project}-nfl-train-win-probability-model"
  requires_compatibilities = ["EC2"] # training is EC2-only now (sfn-training-orchestrator.tf); Fargate training was retired
  network_mode             = "awsvpc"
  cpu                      = local.training_task_cpu
  memory                   = local.training_task_memory
  execution_role_arn       = aws_iam_role.ecs_pipeline.arn
  task_role_arn            = aws_iam_role.ecs_pipeline.arn

  container_definitions = jsonencode([
    {
      name      = "nfl-train-win-probability-model"
      image     = "${var.ecr_repo_url}:nfl-train-win-probability-model-latest"
      essential = true
      environment = [
        { name = "MODEL_ARTIFACTS_BUCKET_NAME", value = aws_s3_bucket.model_artifacts.bucket },
        { name = "AWS_REGION", value = var.region },
        # Every adapter's outer search already parallelizes across
        # processes via n_jobs=-1 (one process per available vCPU, each
        # fitting one candidate single-threaded -- see model_types.py's
        # own n_jobs=1 on each estimator). Without these, numpy/scipy's
        # BLAS backend would also try to multi-thread inside each process,
        # oversubscribing this task's vCPU count.
        { name = "OMP_NUM_THREADS", value = "1" },
        { name = "OPENBLAS_NUM_THREADS", value = "1" },
        { name = "MKL_NUM_THREADS", value = "1" },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.nfl_train_win_probability_model.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "train-win-probability-model"
        }
      }
    }
  ])

  tags = merge(local.common_tags, {
    Sport     = "nfl"
    Component = "training"
  })
}

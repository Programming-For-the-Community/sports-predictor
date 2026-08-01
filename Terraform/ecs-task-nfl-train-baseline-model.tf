# 30-day retention, same rationale as ecs-task-nfl-backfill.tf.
resource "aws_cloudwatch_log_group" "nfl_train_baseline_model" {
  name              = "/ecs/${var.project}-nfl-train-baseline-model"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "nfl"
    Component = "training"
  })
}

# Standalone Fargate task (launched via `aws ecs run-task`, never
# scheduled -- this is a comparison baseline, not a production model).
# Reuses the exact same image as ecs-task-nfl-train-model.tf (both
# scripts live in one Dockerfile, see Source/model-training/nfl/Dockerfile)
# and just overrides the container command to run
# train_baseline_model.py instead of the default train_model.py.
#
# Uses the shared aws_iam_role.ecs_pipeline (iam-ecs-pipeline.tf) -- its
# S3 permissions are scoped to the model artifacts bucket generically
# (any model_name prefix under nfl/*), not to "win-probability"
# specifically, so this baseline's own model_name
# (win-probability-logistic) needs no IAM changes.
#
# cpu=512/memory=1024 (well below train-model's 2048/4096) -- the
# logistic regression search is a 22-combination grid over ~2,700 rows,
# not a 300-candidate XGBoost search; there's no equivalent need for
# multiple cores to spread hundreds of fits across.
resource "aws_ecs_task_definition" "nfl_train_baseline_model" {
  family                   = "${var.project}-nfl-train-baseline-model"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.ecs_pipeline.arn
  task_role_arn            = aws_iam_role.ecs_pipeline.arn

  container_definitions = jsonencode([
    {
      name      = "nfl-train-baseline-model"
      image     = "${var.ecr_repo_url}:nfl-train-model-latest"
      command   = ["train_baseline_model.py"]
      essential = true
      environment = [
        { name = "MODEL_ARTIFACTS_BUCKET_NAME", value = aws_s3_bucket.model_artifacts.bucket },
        { name = "AWS_REGION", value = var.region },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.nfl_train_baseline_model.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "train-baseline-model"
        }
      }
    }
  ])

  tags = merge(local.common_tags, {
    Sport     = "nfl"
    Component = "training"
  })
}

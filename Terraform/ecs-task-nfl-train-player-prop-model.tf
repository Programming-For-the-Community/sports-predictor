# 30-day retention, same rationale as ecs-task-nfl-backfill.tf.
resource "aws_cloudwatch_log_group" "nfl_train_player_prop_model" {
  name              = "/ecs/${var.project}-nfl-train-player-prop-model"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "nfl"
    Component = "training"
  })
}

# Standalone Fargate task, one definition shared by every player-prop
# stat -- see scheduler-nfl-train-player-prop-model.tf, which schedules
# it once per stat in nfl_player_prop_stats via a per-schedule TARGET_STAT
# override, same mechanism described below. Every bit as runnable
# manually via `aws ecs run-task` with your own override if you want to
# retrain and inspect one stat without waiting for Wednesday.
# Reuses the exact same image as ecs-task-nfl-train-model.tf (all four
# training scripts live in one Dockerfile, see
# Source/model-training/nfl/Dockerfile) and overrides the container
# command to run train_player_prop_model.py instead of the default
# train_model.py.
#
# Deliberately does NOT set TARGET_STAT here -- it's the one thing that
# varies between a passing-yards run and a rushing-yards run, so it's
# passed as a `containerOverrides[].environment` override on each
# invocation (scheduled or manual) instead of being baked into the task
# definition. Running the task without that override fails loudly
# (train_player_prop_model.py reads it via os.environ["TARGET_STAT"],
# raising KeyError) rather than silently training an unintended stat.
#
# Uses the shared aws_iam_role.ecs_pipeline (iam-ecs-pipeline.tf) -- its
# S3 permissions are already scoped generically to the model artifacts
# bucket (both player_features.parquet under nfl/training-data/ and every
# model_name's own nfl/<model_name>/ prefix), so no IAM changes were
# needed for this task.
#
# cpu=4096/memory=16384 -- same hyperparameter search shape (and the
# same quota-driven 4 vCPU step-down -- see ecs-task-nfl-train-model.tf's
# comment) as ecs-task-nfl-train-model.tf, so the same compute sizing
# applies regardless of which stat is being trained.
resource "aws_ecs_task_definition" "nfl_train_player_prop_model" {
  family                   = "${var.project}-nfl-train-player-prop-model"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "4096"
  memory                   = "16384"
  execution_role_arn       = aws_iam_role.ecs_pipeline.arn
  task_role_arn            = aws_iam_role.ecs_pipeline.arn

  container_definitions = jsonencode([
    {
      name      = "nfl-train-player-prop-model"
      image     = "${var.ecr_repo_url}:nfl-train-model-latest"
      command   = ["train_player_prop_model.py"]
      essential = true
      environment = [
        { name = "MODEL_ARTIFACTS_BUCKET_NAME", value = aws_s3_bucket.model_artifacts.bucket },
        { name = "AWS_REGION", value = var.region },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.nfl_train_player_prop_model.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "train-player-prop-model"
        }
      }
    }
  ])

  tags = merge(local.common_tags, {
    Sport     = "nfl"
    Component = "training"
  })
}

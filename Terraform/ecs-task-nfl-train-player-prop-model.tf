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
# override. Runnable manually via `aws ecs run-task` with your own override.
# Reuses the same image as ecs-task-nfl-train-win-probability-model.tf (all
# four training scripts live in one Dockerfile, see
# Source/model-training/nfl/Dockerfile) and overrides the container
# command to run train_player_prop_model.py.
#
# Does not set TARGET_STAT here -- it varies per run, so it's passed as a
# `containerOverrides[].environment` override on each invocation instead
# of baked into the task definition. train_player_prop_model.py reads it
# via os.environ["TARGET_STAT"], raising KeyError if it's missing.
#
# Uses the shared aws_iam_role.ecs_pipeline (iam-ecs-pipeline.tf). Same
# cpu/memory sizing as ecs-task-nfl-train-win-probability-model.tf.
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
      image     = "${var.ecr_repo_url}:nfl-train-win-probability-model-latest"
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

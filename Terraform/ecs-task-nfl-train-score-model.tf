# 30-day retention, same rationale as ecs-task-nfl-backfill.tf.
resource "aws_cloudwatch_log_group" "nfl_train_score_model" {
  name              = "/ecs/${var.project}-nfl-train-score-model"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "nfl"
    Component = "training"
  })
}

# Standalone Fargate task, one definition shared by all three score
# targets (margin, home_score, away_score) -- see
# scheduler-nfl-train-score-model.tf, which schedules it once per target
# in nfl_score_targets via a per-schedule SCORE_TARGET override, same
# mechanism as scheduler-nfl-train-player-prop-model.tf. Every bit as
# runnable manually via `aws ecs run-task` with your own override.
#
# Reuses the exact same image as ecs-task-nfl-train-model.tf (all four
# training scripts live in one Dockerfile, see
# Source/model-training/nfl/Dockerfile) and overrides the container
# command to run train_score_model.py instead of the default
# train_model.py. Reads the same event_features.parquet as the
# win-probability task -- no separate feature engineering dependency.
#
# Deliberately does NOT set SCORE_TARGET here, same reasoning as
# TARGET_STAT on the player-prop task definition -- it's the one thing
# that varies between a margin run and a home-score run, so it's passed
# as a `containerOverrides[].environment` override on each invocation
# instead of being baked into the task definition. Running the task
# without that override fails loudly (train_score_model.py reads it via
# os.environ["SCORE_TARGET"], raising KeyError) rather than silently
# training an unintended target.
#
# Uses the shared aws_iam_role.ecs_pipeline (iam-ecs-pipeline.tf). Same
# cpu/memory sizing as ecs-task-nfl-train-model.tf -- identical
# hyperparameter search shape (PARAM_DISTRIBUTIONS, SEARCH_ITERATIONS,
# CV_SPLITS), so the same compute rationale applies.
resource "aws_ecs_task_definition" "nfl_train_score_model" {
  family                   = "${var.project}-nfl-train-score-model"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "8192"
  memory                   = "16384"
  execution_role_arn       = aws_iam_role.ecs_pipeline.arn
  task_role_arn            = aws_iam_role.ecs_pipeline.arn

  container_definitions = jsonencode([
    {
      name      = "nfl-train-score-model"
      image     = "${var.ecr_repo_url}:nfl-train-model-latest"
      command   = ["train_score_model.py"]
      essential = true
      environment = [
        { name = "MODEL_ARTIFACTS_BUCKET_NAME", value = aws_s3_bucket.model_artifacts.bucket },
        { name = "AWS_REGION", value = var.region },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.nfl_train_score_model.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "train-score-model"
        }
      }
    }
  ])

  tags = merge(local.common_tags, {
    Sport     = "nfl"
    Component = "training"
  })
}

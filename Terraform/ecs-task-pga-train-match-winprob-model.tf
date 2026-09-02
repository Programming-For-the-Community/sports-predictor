# 30-day retention so logs don't grow unbounded.
resource "aws_cloudwatch_log_group" "pga_train_match_winprob_model" {
  name              = "/ecs/${var.project}-pga-train-match-winprob-model"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "pga"
    Component = "training"
  })
}

# Standalone Fargate task -- trains the individual match win-probability
# classifier (Ryder Cup/Presidents Cup/WGC Match Play matches), reading
# match_features.parquet (individual-match grain -- see feature-
# engineering/pga/build_dataset.py's build_match_and_cup_datasets).
# Reuses the train-top10-model task's own image and overrides the
# container command, same shared-image/command-override pattern every
# other PGA train-*-model task definition uses.
resource "aws_ecs_task_definition" "pga_train_match_winprob_model" {
  family                   = "${var.project}-pga-train-match-winprob-model"
  requires_compatibilities = ["EC2"] # training is EC2-only now (sfn-training-orchestrator.tf); Fargate training was retired
  network_mode             = "awsvpc"
  cpu                      = local.training_task_cpu
  memory                   = local.training_task_memory
  execution_role_arn       = aws_iam_role.ecs_pipeline.arn
  task_role_arn            = aws_iam_role.ecs_pipeline.arn

  container_definitions = jsonencode([
    {
      name      = "pga-train-match-winprob-model"
      image     = "${var.ecr_repo_url}:pga-train-top10-model-latest"
      command   = ["train_match_winprob_model.py"]
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
          "awslogs-group"         = aws_cloudwatch_log_group.pga_train_match_winprob_model.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "train-match-winprob-model"
        }
      }
    }
  ])

  tags = merge(local.common_tags, {
    Sport     = "pga"
    Component = "training"
  })
}

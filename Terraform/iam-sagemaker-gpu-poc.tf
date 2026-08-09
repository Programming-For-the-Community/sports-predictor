# One-off role for the SageMaker GPU training benchmark (Source/model-
# training/ncaafb/sagemaker_gpu_poc/) -- NOT part of the regular training
# pipeline, no orchestrator/Terraform resource ever assumes this
# automatically. You invoke it yourself via invoke.py to manually kick off
# a single SageMaker Training Job and see whether GPU acceleration is
# worth pursuing further before any real re-architecture.
#
# Scoped to exactly what that one training job needs: read the NCAAFB
# player-prop training data and write model artifacts (same S3 prefix
# iam-ecs-pipeline.tf's shared role already covers, just not reusing that
# role -- SageMaker's own trust policy is different from ECS's, and this
# is deliberately narrower/temporary rather than folded into permanent
# shared infrastructure), plus the CloudWatch Logs permissions every
# SageMaker training job needs to stream its own output.
data "aws_iam_policy_document" "sagemaker_gpu_poc_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["sagemaker.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "sagemaker_gpu_poc" {
  name               = "${var.project}-sagemaker-gpu-poc-exec"
  assume_role_policy = data.aws_iam_policy_document.sagemaker_gpu_poc_assume.json

  tags = merge(local.common_tags, {
    Sport     = "ncaa-fb"
    Component = "training"
  })
}

data "aws_iam_policy_document" "sagemaker_gpu_poc_permissions" {
  statement {
    sid       = "ReadWriteModelArtifacts"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["arn:aws:s3:::${local.model_artifacts_bucket}/*"]
  }

  statement {
    sid       = "ListModelArtifactsNcaafbPrefix"
    actions   = ["s3:ListBucket"]
    resources = ["arn:aws:s3:::${local.model_artifacts_bucket}"]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["ncaafb/*"]
    }
  }

  # Every SageMaker Training Job's own requirement to stream its output --
  # not scoped to one specific log group ARN since the job's own log
  # stream name isn't known until the job actually starts.
  statement {
    sid       = "WriteTrainingJobLogs"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
    resources = ["arn:aws:logs:${var.region}:${var.account_id}:log-group:/aws/sagemaker/*"]
  }
}

resource "aws_iam_role_policy" "sagemaker_gpu_poc_permissions" {
  name   = "${var.project}-sagemaker-gpu-poc-permissions"
  role   = aws_iam_role.sagemaker_gpu_poc.id
  policy = data.aws_iam_policy_document.sagemaker_gpu_poc_permissions.json
}

# Stores serialized model files written by the Fargate training task and
# read by the inference Lambda. Object keys encode the sport and model
# version (e.g. nfl/v3/model.xgb) so old versions are preserved by path
# rather than S3 version history -- this keeps retrieval predictable and
# avoids version-management overhead on what are already small files.
resource "aws_s3_bucket" "model_artifacts" {
  bucket        = local.model_artifacts_bucket
  force_destroy = false

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "training"
  })
}

resource "aws_s3_bucket_public_access_block" "model_artifacts" {
  bucket = aws_s3_bucket.model_artifacts.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "model_artifacts" {
  bucket = aws_s3_bucket.model_artifacts.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

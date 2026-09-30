# Stores serialized model files written by the Fargate training task and
# read by the inference Lambda. Object keys encode the sport, the specific
# model (e.g. win-probability, score-margin, or a per-stat player prop like
# passing-yards), and that model's own version (e.g.
# nfl/win-probability/v3/model.xgb); old versions are preserved by path
# rather than S3 version history. Versioning is per-model, not one shared
# counter across every model a sport has.
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

# Keeps overwritten or deleted model artifacts (promoted model cards,
# boosters) recoverable for 30 days.
resource "aws_s3_bucket_versioning" "model_artifacts" {
  bucket = aws_s3_bucket.model_artifacts.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "model_artifacts" {
  bucket = aws_s3_bucket.model_artifacts.id

  rule {
    id     = "expire-noncurrent-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 30
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.model_artifacts]
}

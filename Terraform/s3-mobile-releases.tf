# Holds the Android APK, synced here by mobile_sync_deploy.yml as one object
# at a stable key (app/sports-predictor.apk) whose user metadata
# (version-code, version-name, sha256, notes) is how the app and the website
# learn a new release exists. Versioning keeps the previous builds.
#
# Private -- the only reader is CloudFront's /app/* behavior (cloudfront.tf),
# via the same origin access control as the frontend bucket. No
# s3:ListBucket grant, so a missing key is a 403 (CloudFront's 403 page),
# never the frontend's 404-to-index.html fallback.
resource "aws_s3_bucket" "mobile_releases" {
  bucket        = local.mobile_releases_bucket
  force_destroy = false

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "mobile"
  })
}

resource "aws_s3_bucket_public_access_block" "mobile_releases" {
  bucket = aws_s3_bucket.mobile_releases.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "mobile_releases" {
  bucket = aws_s3_bucket.mobile_releases.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "mobile_releases" {
  bucket = aws_s3_bucket.mobile_releases.id

  versioning_configuration {
    status = "Enabled"
  }
}

# Keeps the last 5 superseded builds for rollback.
resource "aws_s3_bucket_lifecycle_configuration" "mobile_releases" {
  bucket = aws_s3_bucket.mobile_releases.id

  rule {
    id     = "keep-recent-builds"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days           = 1
      newer_noncurrent_versions = 5
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }

  depends_on = [aws_s3_bucket_versioning.mobile_releases]
}

data "aws_iam_policy_document" "mobile_releases_bucket" {
  statement {
    sid       = "AllowCloudFrontOACGetObject"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.mobile_releases.arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.main.arn]
    }
  }

  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.mobile_releases.arn, "${aws_s3_bucket.mobile_releases.arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "mobile_releases" {
  bucket = aws_s3_bucket.mobile_releases.id
  policy = data.aws_iam_policy_document.mobile_releases_bucket.json

  depends_on = [aws_s3_bucket_public_access_block.mobile_releases]
}

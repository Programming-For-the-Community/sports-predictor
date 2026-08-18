# Stores the built Flutter web output (`flutter build web`'s build/web/
# directory), synced here by frontend_sync_deploy.yml. Private -- the only
# reader is CloudFront, via the origin access control in cloudfront.tf.
# Same shape as s3-model-artifacts.tf.
resource "aws_s3_bucket" "frontend" {
  bucket        = local.frontend_bucket
  force_destroy = false

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "frontend"
  })
}

resource "aws_s3_bucket_public_access_block" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Grants read access to exactly one caller: CloudFront, via this
# distribution's origin access control (cloudfront.tf) -- not a public
# bucket policy. aws:SourceArn scopes it to this specific distribution,
# not "any CloudFront distribution in the account".
#
# s3:ListBucket (on the bucket itself, not /*) is deliberate, not an
# over-grant -- without it, S3 can't distinguish "object doesn't exist"
# from "you can't see it" on a GetObject miss, so it returns 403 for
# *every* missing key regardless of the real reason. cloudfront.tf's SPA
# deep-link fallback used to key off that 403 (error_code = 403 ->
# index.html) -- but CloudFront's own geo-restriction block ALSO returns
# 403 (confirmed against AWS's own georestrictions.html docs, which
# cross-reference custom-error-pages.html as how to customize that exact
# response), and custom_error_response is distribution-wide, not scoped
# to one cache behavior. That meant the SPA fallback was silently
# rewriting every geo-blocked 403 into a 200 index.html response too --
# a real, confirmed way the "US-only" restriction was doing nothing.
# Granting ListBucket makes a missing key return a genuine 404 instead,
# so the SPA fallback can key off 404 (see cloudfront.tf's own
# custom_error_response) and leave 403 alone for actual denials.
data "aws_iam_policy_document" "frontend_bucket" {
  statement {
    sid       = "AllowCloudFrontOACGetObject"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.frontend.arn}/*"]

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
    sid       = "AllowCloudFrontOACListBucket"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.frontend.arn]

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
}

resource "aws_s3_bucket_policy" "frontend" {
  bucket = aws_s3_bucket.frontend.id
  policy = data.aws_iam_policy_document.frontend_bucket.json
}

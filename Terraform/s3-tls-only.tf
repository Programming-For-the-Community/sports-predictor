# Denies any request to these buckets that isn't made over TLS. The
# frontend bucket carries the same statement inside its own policy
# (s3-frontend.tf), since a bucket can only have one policy.
locals {
  tls_only_buckets = {
    model_artifacts = aws_s3_bucket.model_artifacts
    raw_data_lake   = aws_s3_bucket.raw_data_lake
  }
}

data "aws_iam_policy_document" "tls_only" {
  for_each = local.tls_only_buckets

  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [each.value.arn, "${each.value.arn}/*"]

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

resource "aws_s3_bucket_policy" "tls_only" {
  for_each = local.tls_only_buckets

  bucket = each.value.id
  policy = data.aws_iam_policy_document.tls_only[each.key].json

  # S3 rejects a bucket policy while the public access block is still
  # being applied to a new bucket.
  depends_on = [
    aws_s3_bucket_public_access_block.model_artifacts,
    aws_s3_bucket_public_access_block.raw_data_lake,
  ]
}

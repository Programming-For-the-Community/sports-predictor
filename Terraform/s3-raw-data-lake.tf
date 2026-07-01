# Stores raw API responses from each sport's ingest Lambda. The normalize
# Lambda reads from here after each ingest run (triggered by S3 event
# notification -- that notification is wired in the Lambda pass, not here,
# since the Lambda ARN doesn't exist yet).
#
# Account ID is appended to the bucket name to guarantee global uniqueness,
# matching the convention used for the Terraform state backend bucket.
resource "aws_s3_bucket" "raw_data_lake" {
  bucket        = local.raw_bucket_name
  force_destroy = false

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "ingestion"
  })
}

resource "aws_s3_bucket_public_access_block" "raw_data_lake" {
  bucket = aws_s3_bucket.raw_data_lake.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "raw_data_lake" {
  bucket = aws_s3_bucket.raw_data_lake.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Raw files accumulate quickly across six sports' daily/weekly ingest runs.
# Transition to Infrequent Access after 90 days (normalized data is already
# in DynamoDB by then, so raw files are only needed for re-normalization) and
# expire after a year.
resource "aws_s3_bucket_lifecycle_configuration" "raw_data_lake" {
  bucket = aws_s3_bucket.raw_data_lake.id

  rule {
    id     = "age-out-raw-files"
    status = "Enabled"

    filter {
      prefix = ""
    }

    transition {
      days          = 90
      storage_class = "STANDARD_IA"
    }

    expiration {
      days = 365
    }
  }
}

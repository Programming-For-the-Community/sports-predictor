# The raw data lake bucket's entire S3 event notification configuration,
# in one resource -- aws_s3_bucket_notification sets a bucket's whole
# notification config on every apply, so every sport's normalize trigger
# has to be one lambda_function block here rather than one
# aws_s3_bucket_notification resource per sport (which would silently
# overwrite each other). Onboarding a new sport adds one more block plus
# an entry in depends_on, not a new file.
resource "aws_s3_bucket_notification" "raw_data_lake" {
  bucket = aws_s3_bucket.raw_data_lake.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.nfl_normalize.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "nfl/"
    filter_suffix       = ".json"
  }

  lambda_function {
    lambda_function_arn = aws_lambda_function.ncaafb_normalize.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "ncaafb/"
    filter_suffix       = ".json"
  }

  lambda_function {
    lambda_function_arn = aws_lambda_function.nba_normalize.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "nba/"
    filter_suffix       = ".json"
  }

  depends_on = [
    aws_lambda_permission.s3_invoke_nfl_normalize,
    aws_lambda_permission.s3_invoke_ncaafb_normalize,
    aws_lambda_permission.s3_invoke_nba_normalize,
  ]
}

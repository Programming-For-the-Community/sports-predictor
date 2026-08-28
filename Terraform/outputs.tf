# Values produced after apply that downstream resources and the frontend
# build need but can't know ahead of time. Split by sport into
# outputs-nfl.tf/outputs-ncaafb.tf/outputs-nba.tf/outputs-ncaambb.tf; this file holds the
# shared/core outputs common to all sports.

output "cognito_user_pool_id" {
  description = "Cognito User Pool ID -- needed in the frontend SDK config"
  value       = aws_cognito_user_pool.main.id
}

output "cognito_user_pool_arn" {
  description = "Cognito User Pool ARN -- used by the API Gateway Cognito authorizer"
  value       = aws_cognito_user_pool.main.arn
}

output "cognito_client_id" {
  description = "Cognito App Client ID -- needed in the frontend SDK config"
  value       = aws_cognito_user_pool_client.web.id
}

output "api_endpoint" {
  description = "The app's one public URL -- frontend at the root, API under /nfl/* -- both served via CloudFront (see cloudfront.tf)"
  value       = "https://${local.domain}"
}

output "frontend_bucket_name" {
  description = "Frontend static site S3 bucket name -- passed to frontend_sync_deploy.yml for `aws s3 sync`"
  value       = aws_s3_bucket.frontend.bucket
}

output "frontend_distribution_id" {
  description = "CloudFront distribution ID -- passed to frontend_sync_deploy.yml for `aws cloudfront create-invalidation`"
  value       = aws_cloudfront_distribution.main.id
}

# Consumed by sport-specific ingest/backfill CI workflows to populate a
# task's RAW_BUCKET_NAME/*_TABLE_NAME environment variables.
output "raw_data_lake_bucket" {
  description = "Raw data lake S3 bucket name"
  value       = local.raw_bucket_name
}

output "model_artifacts_bucket" {
  description = "Model artifacts S3 bucket name"
  value       = local.model_artifacts_bucket
}

output "entities_table_name" {
  description = "Entities DynamoDB table name"
  value       = local.entities_table
}

output "events_table_name" {
  description = "Events DynamoDB table name"
  value       = local.events_table
}

output "player_game_stats_table_name" {
  description = "Player game stats DynamoDB table name"
  value       = local.player_game_stats_table
}

output "team_game_stats_table_name" {
  description = "Team game stats DynamoDB table name"
  value       = local.team_game_stats_table
}

output "season_gate_function_name" {
  description = "Shared season-gate Lambda function name -- passed to shared_lambdas_deploy workflow for `aws lambda update-function-code`"
  value       = aws_lambda_function.season_gate.function_name
}

output "cloudwatch_geo_widget_function_name" {
  description = "Shared cloudwatch-geo-widget Lambda function name -- passed to shared_lambdas_deploy workflow for `aws lambda update-function-code`"
  value       = aws_lambda_function.cloudwatch_geo_widget.function_name
}

# Values produced after apply that downstream resources and the frontend
# build need but can't know ahead of time.

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
  description = "Full API base URL via the custom domain"
  value       = "https://${local.api_domain}"
}

# Consumed by sport-specific ingest/backfill CI workflows (see
# .github/workflows/tf_install.yml's workflow_call outputs) to populate a
# task's RAW_BUCKET_NAME/*_TABLE_NAME environment variables without each
# workflow re-deriving the naming convention from locals.tf itself.
output "raw_data_lake_bucket" {
  description = "Raw data lake S3 bucket name"
  value       = local.raw_bucket_name
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

output "nfl_backfill_task_definition_arn" {
  description = "ARN of the NFL backfill ECS task definition -- pass to `aws ecs run-task --task-definition`"
  value       = aws_ecs_task_definition.nfl_backfill.arn
}

output "nfl_cluster_name" {
  description = "ECS cluster NFL tasks run in -- select this cluster in the console's Run Task screen"
  value       = aws_ecs_cluster.main.name
}

output "nfl_ingest_function_name" {
  description = "NFL ingest Lambda function name -- passed to nfl_data_pipeline workflow for `aws lambda update-function-code`"
  value       = aws_lambda_function.nfl_ingest.function_name
}

output "nfl_normalize_function_name" {
  description = "NFL normalize Lambda function name -- passed to nfl_data_pipeline workflow for `aws lambda update-function-code`"
  value       = aws_lambda_function.nfl_normalize.function_name
}

output "nfl_predict_function_name" {
  description = "NFL predict Lambda function name -- passed to nfl_ai_hosting workflow for `aws lambda update-function-code`"
  value       = aws_lambda_function.nfl_predict.function_name
}

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

# Consumed by sport-specific ingest/backfill CI workflows (see
# .github/workflows/tf_install.yml's workflow_call outputs) to populate a
# task's RAW_BUCKET_NAME/*_TABLE_NAME environment variables without each
# workflow re-deriving the naming convention from locals.tf itself.
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

output "nfl_schedule_sync_function_name" {
  description = "NFL schedule-sync Lambda function name -- passed to nfl_data_pipeline workflow for `aws lambda update-function-code`"
  value       = aws_lambda_function.nfl_schedule_sync.function_name
}

output "nfl_predict_function_name" {
  description = "NFL predict Lambda function name -- passed to nfl_ai_hosting workflow for `aws lambda update-function-code`"
  value       = aws_lambda_function.nfl_predict.function_name
}

output "season_gate_function_name" {
  description = "Shared season-gate Lambda function name -- passed to shared_lambdas_deploy workflow for `aws lambda update-function-code`"
  value       = aws_lambda_function.season_gate.function_name
}

output "nfl_predict_read_function_name" {
  description = "NFL predict-read Lambda function name -- passed to nfl_deploy workflow for `aws lambda update-function-code`"
  value       = aws_lambda_function.nfl_predict_read.function_name
}

output "nfl_live_scores_function_name" {
  description = "NFL live-scores Lambda function name -- passed to nfl_deploy workflow for `aws lambda update-function-code`"
  value       = aws_lambda_function.nfl_live_scores.function_name
}

output "ncaafb_backfill_task_definition_arn" {
  description = "ARN of the NCAAFB backfill ECS task definition -- pass to `aws ecs run-task --task-definition`"
  value       = aws_ecs_task_definition.ncaafb_backfill.arn
}

output "ncaafb_ingest_function_name" {
  description = "NCAAFB ingest Lambda function name -- passed to ncaafb_data_pipeline workflow for `aws lambda update-function-code`"
  value       = aws_lambda_function.ncaafb_ingest.function_name
}

output "ncaafb_normalize_function_name" {
  description = "NCAAFB normalize Lambda function name -- passed to ncaafb_data_pipeline workflow for `aws lambda update-function-code`"
  value       = aws_lambda_function.ncaafb_normalize.function_name
}

output "ncaafb_schedule_sync_function_name" {
  description = "NCAAFB schedule-sync Lambda function name -- passed to ncaafb_data_pipeline workflow for `aws lambda update-function-code`"
  value       = aws_lambda_function.ncaafb_schedule_sync.function_name
}

output "ncaafb_predict_function_name" {
  description = "NCAAFB predict Lambda function name -- passed to ncaafb_ai_hosting workflow for `aws lambda update-function-code`"
  value       = aws_lambda_function.ncaafb_predict.function_name
}

output "ncaafb_predict_read_function_name" {
  description = "NCAAFB predict-read Lambda function name -- passed to ncaafb_deploy workflow for `aws lambda update-function-code`"
  value       = aws_lambda_function.ncaafb_predict_read.function_name
}

output "ncaafb_live_scores_function_name" {
  description = "NCAAFB live-scores Lambda function name -- passed to ncaafb_deploy workflow for `aws lambda update-function-code`"
  value       = aws_lambda_function.ncaafb_live_scores.function_name
}

# NBA (Sub-phase 3A). ingest/normalize/schedule-sync + backfill wired at
# step 4; nba_predict_function_name added early (2026-08-14, ahead of
# step 6) so nba_deploy.yml's deploy_predict_lambda job can point
# nba-predict at nba_ai_hosting.yml's pushed image -- see that workflow's
# own header for why. nba_predict_read_function_name added at step 6
# (inference). live-scores output still gets added once that Lambda's real
# code exists (step 7), same as tf_install.yml's own workflow_call outputs
# block.
output "nba_backfill_task_definition_arn" {
  description = "ARN of the NBA backfill ECS task definition -- pass to `aws ecs run-task --task-definition`"
  value       = aws_ecs_task_definition.nba_backfill.arn
}

output "nba_ingest_function_name" {
  description = "NBA ingest Lambda function name -- passed to nba_data_pipeline workflow for `aws lambda update-function-code`"
  value       = aws_lambda_function.nba_ingest.function_name
}

output "nba_normalize_function_name" {
  description = "NBA normalize Lambda function name -- passed to nba_data_pipeline workflow for `aws lambda update-function-code`"
  value       = aws_lambda_function.nba_normalize.function_name
}

output "nba_schedule_sync_function_name" {
  description = "NBA schedule-sync Lambda function name -- passed to nba_data_pipeline workflow for `aws lambda update-function-code`"
  value       = aws_lambda_function.nba_schedule_sync.function_name
}

output "nba_predict_function_name" {
  description = "NBA predict Lambda function name -- passed to nba_deploy workflow's deploy_predict_lambda job for `aws lambda update-function-code`"
  value       = aws_lambda_function.nba_predict.function_name
}

output "nba_predict_read_function_name" {
  description = "NBA predict-read Lambda function name -- passed to nba_deploy workflow's predict_read_deploy job for `aws lambda update-function-code`"
  value       = aws_lambda_function.nba_predict_read.function_name
}

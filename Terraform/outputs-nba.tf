# NBA-specific outputs -- see outputs.tf for the shared/core outputs.

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

output "nba_live_scores_function_name" {
  description = "NBA live-scores Lambda function name -- passed to nba_deploy workflow's live_scores_deploy job for `aws lambda update-function-code`"
  value       = aws_lambda_function.nba_live_scores.function_name
}

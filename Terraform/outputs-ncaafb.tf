# NCAAFB-specific outputs -- see outputs.tf for the shared/core outputs.

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

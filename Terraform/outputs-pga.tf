# PGA-specific outputs -- see outputs.tf for the shared/core outputs.

output "pga_backfill_task_definition_arn" {
  description = "ARN of the PGA backfill ECS task definition -- pass to `aws ecs run-task --task-definition`"
  value       = aws_ecs_task_definition.pga_backfill.arn
}

output "pga_ingest_function_name" {
  description = "PGA ingest Lambda function name -- passed to pga_data_pipeline workflow for `aws lambda update-function-code`"
  value       = aws_lambda_function.pga_ingest.function_name
}

output "pga_normalize_function_name" {
  description = "PGA normalize Lambda function name -- passed to pga_data_pipeline workflow for `aws lambda update-function-code`"
  value       = aws_lambda_function.pga_normalize.function_name
}

output "pga_schedule_sync_function_name" {
  description = "PGA schedule-sync Lambda function name -- passed to pga_data_pipeline workflow for `aws lambda update-function-code`"
  value       = aws_lambda_function.pga_schedule_sync.function_name
}

output "pga_predict_function_name" {
  description = "PGA predict Lambda function name -- passed to pga_deploy workflow's deploy_predict_lambda job for `aws lambda update-function-code`"
  value       = aws_lambda_function.pga_predict.function_name
}

output "pga_predict_read_function_name" {
  description = "PGA predict-read Lambda function name -- passed to pga_deploy workflow's predict_read_deploy job for `aws lambda update-function-code`"
  value       = aws_lambda_function.pga_predict_read.function_name
}

output "pga_live_scores_function_name" {
  description = "PGA live-scores Lambda function name -- passed to pga_deploy workflow's live_scores_deploy job for `aws lambda update-function-code`"
  value       = aws_lambda_function.pga_live_scores.function_name
}

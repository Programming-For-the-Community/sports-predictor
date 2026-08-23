# NCAA MBB-specific outputs -- see outputs.tf for the shared/core outputs.
# Populated incrementally as each Lambda/ECS resource is created in later
# onboarding steps (mirrors outputs-nba.tf), not written all at once here.

output "ncaambb_backfill_task_definition_arn" {
  description = "ARN of the NCAA MBB backfill ECS task definition -- pass to `aws ecs run-task --task-definition`"
  value       = aws_ecs_task_definition.ncaambb_backfill.arn
}

output "ncaambb_ingest_function_name" {
  description = "NCAA MBB ingest Lambda function name -- passed to ncaambb_data_pipeline workflow for `aws lambda update-function-code`"
  value       = aws_lambda_function.ncaambb_ingest.function_name
}

output "ncaambb_normalize_function_name" {
  description = "NCAA MBB normalize Lambda function name -- passed to ncaambb_data_pipeline workflow for `aws lambda update-function-code`"
  value       = aws_lambda_function.ncaambb_normalize.function_name
}

output "ncaambb_schedule_sync_function_name" {
  description = "NCAA MBB schedule-sync Lambda function name -- passed to ncaambb_data_pipeline workflow for `aws lambda update-function-code`"
  value       = aws_lambda_function.ncaambb_schedule_sync.function_name
}

output "ncaambb_predict_function_name" {
  description = "NCAA MBB predict Lambda function name -- passed to ncaambb_deploy workflow's deploy_predict_lambda job for `aws lambda update-function-code`"
  value       = aws_lambda_function.ncaambb_predict.function_name
}

output "ncaambb_predict_read_function_name" {
  description = "NCAA MBB predict-read Lambda function name -- passed to ncaambb_deploy workflow's predict_read_deploy job for `aws lambda update-function-code`"
  value       = aws_lambda_function.ncaambb_predict_read.function_name
}

output "ncaambb_live_scores_function_name" {
  description = "NCAA MBB live-scores Lambda function name -- passed to ncaambb_deploy workflow's live_scores_deploy job for `aws lambda update-function-code`"
  value       = aws_lambda_function.ncaambb_live_scores.function_name
}

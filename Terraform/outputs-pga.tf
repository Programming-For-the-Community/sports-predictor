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

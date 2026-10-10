# NHL-specific outputs -- see outputs.tf for the shared/core outputs.

output "nhl_backfill_task_definition_arn" {
  description = "ARN of the NHL backfill ECS task definition -- pass to `aws ecs run-task --task-definition`"
  value       = aws_ecs_task_definition.nhl_backfill.arn
}

output "nhl_ingest_function_name" {
  description = "NHL ingest Lambda function name -- passed to the data_pipeline workflow for `aws lambda update-function-code`"
  value       = aws_lambda_function.nhl_ingest.function_name
}

output "nhl_normalize_function_name" {
  description = "NHL normalize Lambda function name -- passed to the data_pipeline workflow for `aws lambda update-function-code`"
  value       = aws_lambda_function.nhl_normalize.function_name
}

output "nhl_schedule_sync_function_name" {
  description = "NHL schedule-sync Lambda function name -- passed to the data_pipeline workflow for `aws lambda update-function-code`"
  value       = aws_lambda_function.nhl_schedule_sync.function_name
}

output "nhl_predict_function_name" {
  description = "NHL predict Lambda function name -- passed to the deploy workflows' predict deploy job for `aws lambda update-function-code`"
  value       = aws_lambda_function.nhl_predict.function_name
}

output "nhl_live_scores_function_name" {
  description = "NHL live-scores Lambda function name -- passed to the deploy workflows' live_scores_deploy job for `aws lambda update-function-code`"
  value       = aws_lambda_function.nhl_live_scores.function_name
}

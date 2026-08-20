# NFL-specific outputs -- see outputs.tf for the shared/core outputs.

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

output "nfl_predict_read_function_name" {
  description = "NFL predict-read Lambda function name -- passed to nfl_deploy workflow for `aws lambda update-function-code`"
  value       = aws_lambda_function.nfl_predict_read.function_name
}

output "nfl_live_scores_function_name" {
  description = "NFL live-scores Lambda function name -- passed to nfl_deploy workflow for `aws lambda update-function-code`"
  value       = aws_lambda_function.nfl_live_scores.function_name
}

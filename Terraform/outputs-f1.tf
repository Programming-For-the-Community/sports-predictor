# F1-specific outputs -- see outputs.tf for the shared/core outputs.

output "f1_backfill_task_definition_arn" {
  description = "ARN of the F1 backfill ECS task definition -- pass to `aws ecs run-task --task-definition`"
  value       = aws_ecs_task_definition.f1_backfill.arn
}

output "f1_ingest_function_name" {
  description = "F1 ingest Lambda function name -- passed to f1_data_pipeline workflow for `aws lambda update-function-code`"
  value       = aws_lambda_function.f1_ingest.function_name
}

output "f1_normalize_function_name" {
  description = "F1 normalize Lambda function name -- passed to f1_data_pipeline workflow for `aws lambda update-function-code`"
  value       = aws_lambda_function.f1_normalize.function_name
}

output "f1_predict_function_name" {
  description = "F1 predict Lambda function name -- passed to f1_deploy workflow's deploy_predict_lambda job for `aws lambda update-function-code`"
  value       = aws_lambda_function.f1_predict.function_name
}

output "f1_predict_read_function_name" {
  description = "F1 predict-read Lambda function name -- passed to f1_deploy workflow's predict_read_deploy job for `aws lambda update-function-code`"
  value       = aws_lambda_function.f1_predict_read.function_name
}

output "f1_live_scores_function_name" {
  description = "F1 live-scores Lambda function name -- passed to f1_deploy workflow's live_scores_deploy job for `aws lambda update-function-code`"
  value       = aws_lambda_function.f1_live_scores.function_name
}

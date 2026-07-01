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

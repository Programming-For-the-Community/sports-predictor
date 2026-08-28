# CloudWatch custom-widget Lambda backing the two geo panels on
# cloudwatch-dashboard-viewer-analytics.tf. Shared/cross-cutting, not
# sport-specific -- same convention lambda-season-gate.tf establishes.
#
# Code is deployed by shared_lambdas_deploy.yml (via `aws lambda
# update-function-code`), not by Terraform, using a placeholder ZIP with
# lifecycle.ignore_changes.

resource "aws_cloudwatch_log_group" "cloudwatch_geo_widget" {
  name              = "/aws/lambda/${var.project}-cloudwatch-geo-widget"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "observability"
  })
}

data "archive_file" "cloudwatch_geo_widget_placeholder" {
  type        = "zip"
  output_path = "${path.module}/cloudwatch-geo-widget-placeholder.zip"
  source {
    content  = "def lambda_handler(event, context): return '<div>placeholder -- deploy via shared_lambdas_deploy workflow</div>'"
    filename = "handler.py"
  }
}

resource "aws_lambda_function" "cloudwatch_geo_widget" {
  function_name = "${var.project}-cloudwatch-geo-widget"
  description   = "CloudWatch custom-widget renderer for the viewer-analytics dashboard's geo panels (accepted-by-state, blocked-by-region)."
  role          = aws_iam_role.lambda_cloudwatch_geo_widget.arn
  runtime       = "python3.12"
  handler       = "handler.lambda_handler"
  # Logs Insights StartQuery/GetQueryResults is polled synchronously
  # in-handler (up to ~8s) -- generous headroom above that, well under
  # CloudWatch's own custom-widget render budget.
  timeout     = 20
  memory_size = 256

  filename         = data.archive_file.cloudwatch_geo_widget_placeholder.output_path
  source_code_hash = data.archive_file.cloudwatch_geo_widget_placeholder.output_base64sha256

  environment {
    variables = {
      # Plain comma-joined log group names -- passed to StartQuery's own
      # logGroupNames parameter, not embedded as SOURCE clauses in the
      # query text (unlike the dashboard-widget queries in cloudwatch-
      # dashboard-viewer-analytics.tf, which have no other way to name
      # multiple sources). Every sport's predict-read log group, one query.
      ACCEPTED_LOG_GROUP_NAMES = join(",", local.viewer_analytics_log_group_names)
      BLOCKED_LOG_GROUP_NAME   = aws_cloudwatch_log_group.cloudfront_edge_access_logs.name
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.cloudwatch_geo_widget.name
  }

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "observability"
  })
}

# CloudWatch itself (rendering the dashboard) is the caller, not another
# Lambda/API -- same aws_lambda_permission shape every other AWS-service
# invoke in this project uses (e.g. lambda-pga-predict-read.tf's own
# apigateway.amazonaws.com grant), principal swapped for the service that
# actually invokes a custom widget.
resource "aws_lambda_permission" "cloudwatch_invoke_geo_widget" {
  statement_id  = "AllowCloudWatchDashboardInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.cloudwatch_geo_widget.function_name
  principal     = "cloudwatch.amazonaws.com"
}

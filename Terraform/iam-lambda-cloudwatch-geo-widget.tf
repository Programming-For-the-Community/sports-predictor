data "aws_iam_policy_document" "lambda_cloudwatch_geo_widget_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda_cloudwatch_geo_widget" {
  name               = "${var.project}-lambda-cloudwatch-geo-widget-exec"
  assume_role_policy = data.aws_iam_policy_document.lambda_cloudwatch_geo_widget_assume.json

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "observability"
  })
}

resource "aws_iam_role_policy_attachment" "lambda_cloudwatch_geo_widget_logs" {
  role       = aws_iam_role.lambda_cloudwatch_geo_widget.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# logs:StartQuery supports resource-level scoping by log-group ARN;
# GetQueryResults/StopQuery act on a queryId, not a log group, so AWS
# gives them no ARN to scope by -- Resource "*" is the actual minimum for
# those two, not an over-broad shortcut.
data "aws_iam_policy_document" "lambda_cloudwatch_geo_widget_logs_insights" {
  statement {
    actions = ["logs:StartQuery"]
    resources = [
      aws_cloudwatch_log_group.nfl_predict_read.arn,
      aws_cloudwatch_log_group.ncaafb_predict_read.arn,
      aws_cloudwatch_log_group.nba_predict_read.arn,
      aws_cloudwatch_log_group.ncaambb_predict_read.arn,
      aws_cloudwatch_log_group.pga_predict_read.arn,
      aws_cloudwatch_log_group.cloudfront_edge_access_logs.arn,
    ]
  }

  statement {
    actions   = ["logs:GetQueryResults", "logs:StopQuery"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "lambda_cloudwatch_geo_widget_logs_insights" {
  name   = "${var.project}-lambda-cloudwatch-geo-widget-logs-insights"
  role   = aws_iam_role.lambda_cloudwatch_geo_widget.id
  policy = data.aws_iam_policy_document.lambda_cloudwatch_geo_widget_logs_insights.json
}

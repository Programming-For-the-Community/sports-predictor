# Rejected traffic on this project's own subnets. The VPC itself is shared
# (var.vpc_id), so logging is scoped per subnet rather than VPC-wide. No
# security group here accepts inbound traffic, so REJECT records are the
# security-relevant ones -- and keep ingestion close to zero.
locals {
  flow_log_subnets = {
    public_1  = aws_subnet.public_1.id
    public_2  = aws_subnet.public_2.id
    public_3  = aws_subnet.public_3.id
    private_a = aws_subnet.private_a.id
    private_b = aws_subnet.private_b.id
    private_c = aws_subnet.private_c.id
  }
}

resource "aws_cloudwatch_log_group" "vpc_flow_logs" {
  name              = "/aws/vpc-flow-logs/${var.project}"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "network"
  })
}

resource "aws_iam_role" "vpc_flow_logs" {
  name = "${var.project}-vpc-flow-logs"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "vpc-flow-logs.amazonaws.com" }
        Action    = "sts:AssumeRole"
        Condition = {
          StringEquals = { "aws:SourceAccount" = var.account_id }
        }
      }
    ]
  })

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "network"
  })
}

data "aws_iam_policy_document" "vpc_flow_logs" {
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
    resources = ["${aws_cloudwatch_log_group.vpc_flow_logs.arn}:*"]
  }
}

resource "aws_iam_role_policy" "vpc_flow_logs" {
  name   = "${var.project}-vpc-flow-logs"
  role   = aws_iam_role.vpc_flow_logs.id
  policy = data.aws_iam_policy_document.vpc_flow_logs.json
}

resource "aws_flow_log" "subnets" {
  for_each = local.flow_log_subnets

  subnet_id                = each.value
  traffic_type             = "REJECT"
  log_destination_type     = "cloud-watch-logs"
  log_destination          = aws_cloudwatch_log_group.vpc_flow_logs.arn
  iam_role_arn             = aws_iam_role.vpc_flow_logs.arn
  max_aggregation_interval = 600

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "network"
    Name      = "${var.project}-${replace(each.key, "_", "-")}-flow-log"
  })
}

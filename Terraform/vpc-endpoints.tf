# Gateway endpoints for S3 and DynamoDB. Gateway endpoints are free (no
# hourly charge) and inject routes directly into route tables, so private
# Lambda and Fargate tasks can reach these services without a NAT Gateway --
# this is the mechanism that makes "no NAT Gateway" viable for the
# architecture (see docs/ARCHITECTURE.md).
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = var.vpc_id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "networking"
    Name      = "${var.project}-s3-endpoint"
  })
}

resource "aws_vpc_endpoint" "dynamodb" {
  vpc_id            = var.vpc_id
  service_name      = "com.amazonaws.${var.region}.dynamodb"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "networking"
    Name      = "${var.project}-dynamodb-endpoint"
  })
}

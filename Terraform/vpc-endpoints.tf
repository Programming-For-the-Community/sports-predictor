# Gateway endpoints for S3 and DynamoDB. Gateway endpoints are free (no
# hourly charge) and inject routes directly into route tables, so private
# Lambda and Fargate tasks can reach these services without a NAT Gateway --
# this is the mechanism that makes "no NAT Gateway" viable for the
# architecture (see docs/ARCHITECTURE.md).
#
# Private route table only -- the EC2 training track's own task traffic
# (sfn-training-orchestrator-ec2.tf's RunTrainingTaskEc2Spot/OnDemand) runs
# its NetworkConfiguration in the PRIVATE subnets specifically so it reaches
# S3/DynamoDB through these endpoints, not a public route -- see that file's
# own comment. The EC2 hosts themselves (ec2-training-asg.tf) still launch
# into the public subnets (needed for ECR pull/agent registration, the same
# reason Fargate's own training tasks sit there), but that's the host's
# network path, not the task's; the two are independently configurable for
# EC2 launch type + awsvpc mode, and the task's own traffic never needs to
# touch a public route at all.
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

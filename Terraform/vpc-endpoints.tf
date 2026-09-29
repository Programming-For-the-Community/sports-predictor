# Gateway endpoints for S3 and DynamoDB. Gateway endpoints are free (no
# hourly charge) and inject routes directly into route tables, so private
# Lambda and Fargate tasks can reach these services without a NAT Gateway --
# this is the mechanism that makes "no NAT Gateway" viable for the
# architecture (see docs/ARCHITECTURE.md).
#
# Attached to both route tables, so anything in the VPC reaches S3 and
# DynamoDB over the endpoints rather than the internet gateway: private
# Lambdas and tasks (including the EC2 training track's own task traffic --
# sfn-training-orchestrator.tf runs its NetworkConfiguration in the private
# subnets), and the public-subnet EC2 training hosts (ec2-training-asg.tf) and
# Fargate tasks, whose S3 traffic includes ECR image-layer downloads.
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = var.vpc_id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id, aws_route_table.public.id]

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
  route_table_ids   = [aws_route_table.private.id, aws_route_table.public.id]

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "networking"
    Name      = "${var.project}-dynamodb-endpoint"
  })
}

# No STS Interface Endpoint -- removed after get_account_id() (library/aws/
# account.py) dropped its STS fallback entirely. Every compute resource
# that needs the account ID now gets it from the AWS_ACCOUNT_ID env var
# (var.account_id, wired into every Lambda/ECS task that constructs an
# S3Manager), so nothing in this codebase calls STS anymore -- an Interface
# Endpoint here would be pure unused cost (~$7/month for a single AZ) with
# nothing to route. Re-add if a future dependency genuinely needs STS
# reachability from inside the VPC.

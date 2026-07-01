# Security group for the inference Lambda. No inbound rules are needed --
# Lambda is invoked by API Gateway via the AWS API, not through a network
# port. Outbound HTTPS allows the function to reach DynamoDB and S3 via the
# VPC Gateway Endpoints in vpc-endpoints.tf.
resource "aws_security_group" "lambda_inference" {
  name        = "${var.project}-lambda-inference"
  description = "Inference Lambda -- outbound HTTPS to VPC endpoints only"
  vpc_id      = var.vpc_id

  egress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
    description = "HTTPS to DynamoDB and S3 via VPC Gateway Endpoints"
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "serving"
  })
}

# Security group for the Feature Engineering and Train Model Fargate tasks.
# No inbound -- tasks are launched by EventBridge or Step Functions via
# ECS RunTask, not through a network port. Outbound HTTPS covers DynamoDB
# and S3 via Gateway Endpoints and ECR image pulls.
#
# Note: Fargate pulling ECR images from private subnets requires either a
# NAT Gateway (deliberately excluded -- see docs/ARCHITECTURE.md) or ECR
# VPC Interface Endpoints (com.amazonaws.<region>.ecr.api and .ecr.dkr).
# Add those endpoints before deploying Fargate tasks to private subnets.
resource "aws_security_group" "ecs_pipeline" {
  name        = "${var.project}-ecs-pipeline"
  description = "Feature Engineering and Training Fargate tasks -- outbound HTTPS to VPC endpoints"
  vpc_id      = var.vpc_id

  egress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
    description = "HTTPS to DynamoDB, S3, and ECR via VPC endpoints"
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "training"
  })
}

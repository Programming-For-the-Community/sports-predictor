# Three private subnets spread across three availability zones. Lambda and
# Fargate resources deploy here. No route to the internet -- private resources
# reach S3 and DynamoDB via the VPC Gateway Endpoints in vpc-endpoints.tf,
# which is why no NAT Gateway is needed (see docs/ARCHITECTURE.md).
resource "aws_subnet" "private_a" {
  vpc_id            = var.vpc_id
  cidr_block        = var.private1_subnet_cidr
  availability_zone = "${var.region}a"

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "networking"
    Name      = "${var.project}-private-a"
  })
}

resource "aws_subnet" "private_b" {
  vpc_id            = var.vpc_id
  cidr_block        = var.private2_subnet_cidr
  availability_zone = "${var.region}b"

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "networking"
    Name      = "${var.project}-private-b"
  })
}

resource "aws_subnet" "private_c" {
  vpc_id            = var.vpc_id
  cidr_block        = var.private3_subnet_cidr
  availability_zone = "${var.region}c"

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "networking"
    Name      = "${var.project}-private-c"
  })
}

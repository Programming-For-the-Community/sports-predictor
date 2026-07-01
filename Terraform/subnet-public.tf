# Three public subnets spread across three availability zones. Nothing in the
# current architecture deploys here directly -- these exist for any future
# load balancer or NAT gateway if one is ever added. The route table sends
# 0.0.0.0/0 to the IGW created in networking.tf.
resource "aws_subnet" "public_1" {
  vpc_id                  = var.vpc_id
  cidr_block              = var.public1_subnet_cidr
  availability_zone       = "${var.region}a"
  map_public_ip_on_launch = false

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "networking"
    Name      = "${var.project}-public-1"
  })
}

resource "aws_subnet" "public_2" {
  vpc_id                  = var.vpc_id
  cidr_block              = var.public2_subnet_cidr
  availability_zone       = "${var.region}b"
  map_public_ip_on_launch = false

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "networking"
    Name      = "${var.project}-public-2"
  })
}

resource "aws_subnet" "public_3" {
  vpc_id                  = var.vpc_id
  cidr_block              = var.public3_subnet_cidr
  availability_zone       = "${var.region}c"
  map_public_ip_on_launch = false

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "networking"
    Name      = "${var.project}-public-3"
  })
}

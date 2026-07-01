# Three private subnets spread across three availability zones. Lambda and
# Fargate resources deploy here. No route to the internet -- private resources
# reach S3 and DynamoDB via the VPC Gateway Endpoints in vpc-endpoints.tf,
# which is why no NAT Gateway is needed (see docs/ARCHITECTURE.md).
resource "aws_subnet" "private_a" {
  vpc_id            = data.aws_vpc.main.id
  cidr_block        = var.private1_subnet_cidr
  availability_zone = data.aws_availability_zones.available.names[0]

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "networking"
    Name      = "${var.project}-private-a"
  })
}

resource "aws_subnet" "private_b" {
  vpc_id            = data.aws_vpc.main.id
  cidr_block        = var.private2_subnet_cidr
  availability_zone = data.aws_availability_zones.available.names[1]

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "networking"
    Name      = "${var.project}-private-b"
  })
}

resource "aws_subnet" "private_c" {
  vpc_id            = data.aws_vpc.main.id
  cidr_block        = var.private3_subnet_cidr
  availability_zone = data.aws_availability_zones.available.names[2]

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "networking"
    Name      = "${var.project}-private-c"
  })
}

resource "aws_route_table" "private" {
  vpc_id = data.aws_vpc.main.id

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "networking"
    Name      = "${var.project}-private"
  })
}

resource "aws_route_table_association" "private_a" {
  subnet_id      = aws_subnet.private_a.id
  route_table_id = aws_route_table.private.id
}

resource "aws_route_table_association" "private_b" {
  subnet_id      = aws_subnet.private_b.id
  route_table_id = aws_route_table.private.id
}

resource "aws_route_table_association" "private_c" {
  subnet_id      = aws_subnet.private_c.id
  route_table_id = aws_route_table.private.id
}

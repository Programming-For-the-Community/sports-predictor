# Three public subnets spread across three availability zones. Nothing in the
# current architecture deploys here directly -- these exist for any future
# load balancer or NAT gateway if one is ever added. The route table sends
# 0.0.0.0/0 to the existing IGW on the VPC (looked up in networking.tf).
resource "aws_subnet" "public_1" {
  vpc_id                  = data.aws_vpc.main.id
  cidr_block              = var.public1_subnet_cidr
  availability_zone       = data.aws_availability_zones.available.names[0]
  map_public_ip_on_launch = false

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "networking"
    Name      = "${var.project}-public-1"
  })
}

resource "aws_subnet" "public_2" {
  vpc_id                  = data.aws_vpc.main.id
  cidr_block              = var.public2_subnet_cidr
  availability_zone       = data.aws_availability_zones.available.names[1]
  map_public_ip_on_launch = false

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "networking"
    Name      = "${var.project}-public-2"
  })
}

resource "aws_subnet" "public_3" {
  vpc_id                  = data.aws_vpc.main.id
  cidr_block              = var.public3_subnet_cidr
  availability_zone       = data.aws_availability_zones.available.names[2]
  map_public_ip_on_launch = false

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "networking"
    Name      = "${var.project}-public-3"
  })
}

resource "aws_route_table" "public" {
  vpc_id = data.aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = data.aws_internet_gateway.main.id
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "networking"
    Name      = "${var.project}-public"
  })
}

resource "aws_route_table_association" "public_1" {
  subnet_id      = aws_subnet.public_1.id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "public_2" {
  subnet_id      = aws_subnet.public_2.id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "public_3" {
  subnet_id      = aws_subnet.public_3.id
  route_table_id = aws_route_table.public.id
}

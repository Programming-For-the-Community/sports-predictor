# Data sources that translate the CIDR-based variables into subnet and VPC
# IDs usable by Lambda, Fargate, and security group resources. Defined here
# once so every other resource references data.aws_subnet.private_a.id etc.
# rather than performing its own lookup.
#
# These also act as early validation -- if any CIDR doesn't exist in the
# target VPC, terraform plan fails here rather than mid-apply.
data "aws_vpc" "main" {
  id = var.vpc_id
}

data "aws_subnet" "private_a" {
  vpc_id     = data.aws_vpc.main.id
  cidr_block = var.private1_subnet_cidr
}

data "aws_subnet" "private_b" {
  vpc_id     = data.aws_vpc.main.id
  cidr_block = var.private2_subnet_cidr
}

data "aws_subnet" "private_c" {
  vpc_id     = data.aws_vpc.main.id
  cidr_block = var.private3_subnet_cidr
}

data "aws_subnet" "public_1" {
  vpc_id     = data.aws_vpc.main.id
  cidr_block = var.public1_subnet_cidr
}

data "aws_subnet" "public_2" {
  vpc_id     = data.aws_vpc.main.id
  cidr_block = var.public2_subnet_cidr
}

data "aws_subnet" "public_3" {
  vpc_id     = data.aws_vpc.main.id
  cidr_block = var.public3_subnet_cidr
}

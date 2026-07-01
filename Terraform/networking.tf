# Subnets are managed as Terraform resources in subnet-private.tf and
# subnet-public.tf, so there are no data-source lookups here. Downstream
# resources reference aws_subnet.private_a.id etc. directly.
data "aws_vpc" "main" {
  id = var.vpc_id
}

# Used by the public route table in subnet-public.tf to route 0.0.0.0/0
# traffic to the internet. Looks up the IGW already attached to the VPC
# rather than creating a new one.
data "aws_internet_gateway" "main" {
  filter {
    name   = "attachment.vpc-id"
    values = [var.vpc_id]
  }
}

# Used to spread subnets across availability zones without hardcoding AZ names.
data "aws_availability_zones" "available" {
  state = "available"
}

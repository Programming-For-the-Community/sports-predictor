resource "aws_internet_gateway" "main" {
  vpc_id = var.vpc_id

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "networking"
    Name      = "${var.project}-igw"
  })
}

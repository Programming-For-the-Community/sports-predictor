terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.84"
    }
  }

  backend "s3" {
    bucket = "sports-predictor-tfstate-048908104884"
    key    = "sports-predictor.tfstate"
    region = "us-east-2"
  }
}

provider "aws" {
  region = var.region
}
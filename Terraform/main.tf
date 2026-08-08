terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.84"
    }
    # Used once, in iam-stepfunctions-orchestrator.tf -- a short buffer
    # between the IAM permissions training_orchestrator's logging_configuration
    # needs and the state machine update that actually uses them, since
    # IAM/CloudWatch-Logs-resource-policy changes are eventually consistent
    # (a completed Terraform apply of the policy doesn't guarantee AWS has
    # finished propagating it yet).
    time = {
      source  = "hashicorp/time"
      version = "~> 0.11"
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

# CloudFront (cloudfront.tf) requires its ACM certificate in us-east-1
# regardless of the stack's primary region -- see acm.tf.
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
}
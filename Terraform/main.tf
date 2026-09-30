terraform {
  # 1.10+ for the S3 backend's native lockfile (use_lockfile below).
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.84"
    }
    # Used in iam-stepfunctions-orchestrator.tf as a short buffer for IAM/
    # CloudWatch-Logs-resource-policy propagation before the state machine
    # update that depends on it.
    time = {
      source  = "hashicorp/time"
      version = "~> 0.11"
    }
    # Generates the CloudFront-to-API-Gateway origin secret (waf-api-gateway.tf).
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  backend "s3" {
    bucket = "sports-predictor-tfstate-048908104884"
    key    = "sports-predictor.tfstate"
    region = "us-east-2"
    # Writes sports-predictor.tfstate.tflock beside the state for the
    # duration of each plan/apply, so two runs can't write state at once.
    use_lockfile = true
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
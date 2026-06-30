variable "region" {
  description = "AWS Region to build infrastructure in"
  type        = string
  default     = "us-east-2"
  nullable    = false
}

variable "owner" {
  description = "Owner of the project"
  type        = string
  nullable    = false
  default     = "charlie-hahm"
}

variable "account_id" {
  description = "AWS Account ID"
  type        = string
  nullable    = false
  default     = "1234567890"
}

variable "project" {
  description = "Name of the project"
  type        = string
  nullable    = false
  default     = "sports-predictor"
}

variable "alert_email" {
  description = "Email address that receives AWS Budgets threshold notifications. No default on purpose -- supplied via TF_VAR_alert_email from a GitHub Actions secret, not committed to the repo."
  type        = string
  nullable    = false
}

variable "monthly_limit" {
  description = "Whole-project monthly budget limit in USD"
  type        = string
  default     = "15"
  nullable    = false
}

variable "per_sport_limits" {
  description = "Map of sport (matching the Sport tag value, e.g. \"nfl\") to its monthly USD budget limit. Empty by default -- populate a sport's entry once it has a few months of real cost data to set a threshold against"
  type        = map(string)
  default     = {}
  nullable    = false
}

variable "activate_cost_allocation_tags" {
  description = "Whether Terraform should activate the Project/Sport/Component/Environment cost allocation tags. Leave false until at least one resource carrying each tag has actually been created"
  type        = bool
  default     = false
  nullable    = false
}

variable "environment" {
  description = "Deployment environment, applied as the Environment cost-allocation tag on every resource (see docs/TAGGING_STRATEGY.md)"
  type        = string
  default     = "dev"
  nullable    = false
}
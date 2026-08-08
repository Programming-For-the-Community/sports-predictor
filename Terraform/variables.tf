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

# ── Networking ────────────────────────────────────────────────────────────────

variable "vpc_id" {
  description = "ID of the VPC to deploy into -- supplied via TF_VAR_vpc_id from the VPC_ID GitHub Actions secret"
  type        = string
  nullable    = false
}

variable "vpc_cidr" {
  description = "CIDR block of the VPC -- used to scope security group rules"
  type        = string
  nullable    = false
}

variable "private1_subnet_cidr" {
  description = "CIDR block for private subnet A (SUBNET_A_CIDR)"
  type        = string
  nullable    = false
}

variable "private2_subnet_cidr" {
  description = "CIDR block for private subnet B (SUBNET_B_CIDR)"
  type        = string
  nullable    = false
}

variable "private3_subnet_cidr" {
  description = "CIDR block for private subnet C (SUBNET_C_CIDR)"
  type        = string
  nullable    = false
}

variable "public1_subnet_cidr" {
  description = "CIDR block for public subnet 1 (PUBLIC1_SUBNET_CIDR)"
  type        = string
  nullable    = false
}

variable "public2_subnet_cidr" {
  description = "CIDR block for public subnet 2 (PUBLIC2_SUBNET_CIDR)"
  type        = string
  nullable    = false
}

variable "public3_subnet_cidr" {
  description = "CIDR block for public subnet 3 (PUBLIC3_SUBNET_CIDR)"
  type        = string
  nullable    = false
}

# ── Containers ────────────────────────────────────────────────────────────────

variable "ecr_repo_url" {
  description = "URL of the shared ECR repository that sport-specific images are pushed to. ECR is pre-existing and managed outside this stack (shared across projects, same pattern as the VPC) -- no default on purpose, supplied via TF_VAR_ecr_repo_url from the ECR_URI GitHub Actions secret."
  type        = string
  nullable    = false
}

variable "espn_api_root_url" {
  description = "Root URL of ESPN's public (unofficial) site API, shared by every sport-specific task that uses it -- each task appends its own sport path (e.g. football/nfl). Override via TF_VAR_espn_api_root_url from a GitHub Actions variable if ESPN's domain changes."
  type        = string
  default     = "https://site.api.espn.com/apis/site/v2/sports"
  nullable    = false
}

# ── Training compute budget ──────────────────────────────────────────────────
# Drives local.training_max_concurrency (locals-training-compute.tf), which
# sets TrainAllTargets' MaxConcurrency in sfn-training-orchestrator.tf and
# the cpu/memory on every nfl-train-*-model ECS task definition. Changing
# any one of these four values (e.g. a raised account quota, or a
# per-task vCPU size found to be more than a training run actually uses)
# is the single code change that reflows through to both.

variable "fargate_account_vcpu_limit" {
  description = "Account-wide Fargate on-demand concurrent vCPU quota (as shown in the Service Quotas console for 'Fargate On-Demand vCPU count')"
  type        = number
  default     = 250
  nullable    = false
}

variable "training_vcpu_budget_fraction" {
  description = "Max fraction of fargate_account_vcpu_limit the training orchestrator's concurrent ECS tasks may consume at once, leaving the rest of the quota free for feature-engineering, backfill, and ingest tasks that can run at the same time"
  type        = number
  default     = 0.6 # 3/5
  nullable    = false

  validation {
    condition     = var.training_vcpu_budget_fraction > 0 && var.training_vcpu_budget_fraction <= 1
    error_message = "training_vcpu_budget_fraction must be between 0 (exclusive) and 1 (inclusive)."
  }
}

variable "training_task_vcpu" {
  description = "vCPU allocated to each concurrent training ECS task (win-probability/score/player-prop). Must be a value Fargate actually supports; lowering it raises local.training_max_concurrency for the same vCPU budget"
  type        = number
  default     = 4
  nullable    = false

  validation {
    condition     = contains([0.25, 0.5, 1, 2, 4, 8, 16], var.training_task_vcpu)
    error_message = "training_task_vcpu must be one of Fargate's supported vCPU sizes: 0.25, 0.5, 1, 2, 4, 8, 16."
  }
}

variable "training_task_memory_per_vcpu_mib" {
  description = "Memory (MiB) provisioned per training_task_vcpu unit -- 4096 (4GB/vCPU) matches what a training task's dataset size actually needs, well within Fargate's valid memory range at every training_task_vcpu size this project uses"
  type        = number
  default     = 4096
  nullable    = false
}

variable "training_min_concurrent_tasks" {
  description = "Floor on concurrent training tasks regardless of the vCPU-budget math -- keeps a shrunken vCPU budget or an oversized training_task_vcpu from serializing training down to one task at a time"
  type        = number
  default     = 5
  nullable    = false
}

# ── DNS / TLS ─────────────────────────────────────────────────────────────────

variable "domain_name" {
  description = "Root domain name managed in Route 53"
  type        = string
  nullable    = false
}

variable "hosted_zone_id" {
  description = "Route 53 hosted zone ID for var.domain_name"
  type        = string
  nullable    = false
}


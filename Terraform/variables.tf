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
  description = "Email address that receives AWS Budgets threshold notifications. Supplied via TF_VAR_alert_email from a GitHub Actions secret."
  type        = string
  nullable    = false
}

variable "monthly_limit" {
  description = "Whole-project monthly budget limit in USD"
  type        = string
  default     = "30"
  nullable    = false
}

variable "per_sport_limits" {
  description = "Map of sport (matching the Sport tag value, e.g. \"nfl\") to its monthly USD budget limit"
  type        = map(string)
  default     = {}
  nullable    = false
}

variable "activate_cost_allocation_tags" {
  description = "Whether Terraform should activate the Project/Sport/Component/Environment cost allocation tags"
  type        = bool
  default     = false
  nullable    = false
}

variable "environment" {
  description = "Deployment environment, applied as the Environment cost-allocation tag on every resource (see design/TAGGING_STRATEGY.md)"
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
  description = "URL of the shared ECR repository that sport-specific images are pushed to. Supplied via TF_VAR_ecr_repo_url from the ECR_URI GitHub Actions secret."
  type        = string
  nullable    = false
}

variable "espn_api_root_url" {
  description = "Root URL of ESPN's public site API, shared by every sport-specific task that uses it -- each task appends its own sport path (e.g. football/nfl). Overridable via TF_VAR_espn_api_root_url."
  type        = string
  default     = "https://site.web.api.espn.com/apis/site/v2/sports"
  nullable    = false
}

variable "espn_core_api_root_url" {
  description = "Root URL of ESPN's 'core' API (sports.core.api.espn.com), used for coach and injury enrichment -- see library/http/espn_core.py. Overridable via TF_VAR_espn_core_api_root_url."
  type        = string
  default     = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl"
  nullable    = false
}

variable "ncaambb_espn_core_api_root_url" {
  description = "Root URL of ESPN's 'core' API (sports.core.api.espn.com), scoped to men's college basketball -- used for AP Top 25 poll history (see library/http/ncaambb_core.py). Overridable via TF_VAR_ncaambb_espn_core_api_root_url."
  type        = string
  default     = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/mens-college-basketball"
  nullable    = false
}

variable "espn_user_agent" {
  description = "User-Agent sent on every ESPN request."
  type        = string
  default     = "python-requests/2.31.0"
  nullable    = false
}

variable "cfbd_api_root_url" {
  description = "Root URL of CollegeFootballData.com's REST API v2, used by every NCAAFB task that calls it."
  type        = string
  default     = "https://api.collegefootballdata.com"
  nullable    = false
}

variable "jolpica_api_root_url" {
  description = "Root URL of Jolpica-F1, used by every F1 task that calls it. Overridable via TF_VAR_jolpica_api_root_url -- see library/http/f1.py's own DEFAULT_JOLPICA_API_ROOT_URL."
  type        = string
  default     = "https://api.jolpi.ca/ergast/f1"
  nullable    = false
}

variable "jolpica_user_agent" {
  description = "User-Agent sent on every Jolpica-F1 request. See library/http/f1.py's own DEFAULT_JOLPICA_USER_AGENT."
  type        = string
  default     = "sports-predictor-f1-client/1.0 (personal-use sports analytics; non-commercial)"
  nullable    = false
}

variable "third_party_api_key_secret_arn" {
  description = "ARN of the shared Secrets Manager secret holding every sport's third-party API key as a JSON field (e.g. ncaa_fb_ingest_key, ncaa_fb_backfill_key). Supplied via TF_VAR_third_party_api_key_secret_arn from the THIRD_PARTY_API_KEYS_SECRET_ARN GitHub Actions secret."
  type        = string
  nullable    = false
}

# ── Training compute budget ──────────────────────────────────────────────────
# Drives local.training_max_concurrency (locals-training-compute.tf), which
# sets TrainAllTargets' MaxConcurrency in sfn-training-orchestrator.tf and
# the cpu/memory on every nfl-train-*-model ECS task definition.

variable "fargate_account_vcpu_limit" {
  description = "Account-wide Fargate on-demand concurrent vCPU quota. Consulted by feature-engineering (local.feature_engineering_max_concurrency, locals-feature-engineering-compute.tf). Supplied via TF_VAR_fargate_account_vcpu_limit from the FARGATE_VCPU_LIMIT GitHub Actions variable."
  type        = number
  default     = 250
  nullable    = false
}

variable "training_vcpu_budget_fraction" {
  description = "Max fraction of ec2_spot_account_vcpu_limit the training orchestrator's concurrent Spot ECS tasks may consume at once. Supplied via TF_VAR_training_vcpu_budget_fraction from the VCPU_BUDGET_FRACTION GitHub Actions variable."
  type        = number
  default     = 0.75 # 3/4
  nullable    = false

  validation {
    condition     = var.training_vcpu_budget_fraction > 0 && var.training_vcpu_budget_fraction <= 1
    error_message = "training_vcpu_budget_fraction must be between 0 (exclusive) and 1 (inclusive)."
  }
}

variable "training_task_vcpu" {
  description = "vCPU allocated to each concurrent training ECS task. Must be one of Fargate's supported vCPU sizes."
  type        = number
  default     = 16
  nullable    = false

  validation {
    condition     = contains([0.25, 0.5, 1, 2, 4, 8, 16], var.training_task_vcpu)
    error_message = "training_task_vcpu must be one of Fargate's supported vCPU sizes: 0.25, 0.5, 1, 2, 4, 8, 16."
  }
}

variable "training_task_memory_per_vcpu_mib" {
  description = "Memory (MiB) provisioned per training_task_vcpu unit."
  type        = number
  default     = 4096
  nullable    = false
}

# ── EC2 training compute (locals-training-compute.tf) ────────────────────────
# EC2 is the sole training compute path (ec2-training-asg.tf, sfn-training-
# orchestrator.tf). Only the Spot quota is tracked here -- the on-demand
# fallback ASG has a fixed max_size of 1, independent of any account quota.

variable "ec2_spot_account_vcpu_limit" {
  description = "Account-wide EC2 Spot Standard-instance-family concurrent vCPU quota. Supplied via TF_VAR_ec2_spot_account_vcpu_limit from the EC2_SPOT_VCPU_LIMIT GitHub Actions variable."
  type        = number
  default     = 256
  nullable    = false
}

variable "training_sport_concurrency" {
  description = "How many sports' ForEachSport iterations may run at once (sfn-training-orchestrator.tf)."
  type        = number
  default     = 6
  nullable    = false

  validation {
    condition     = var.training_sport_concurrency >= 1
    error_message = "training_sport_concurrency must be at least 1."
  }
}

# ── Feature-engineering compute ──────────────────────────────────────────────
# Per-sport cpu/memory for each sport's standalone feature-engineering
# Fargate task (ecs-task-<sport>-feature-engineering.tf). No fallback
# default -- a missed entry fails the apply.

variable "feature_engineering_task_cpu" {
  description = "Fargate CPU units (1024 per vCPU) for each sport's feature-engineering task, keyed by sport."
  type        = map(number)
  default = {
    nfl     = 1024
    ncaafb  = 8192
    nba     = 8192
    ncaambb = 8192
    pga     = 1024
    f1      = 1024
  }
  nullable = false

  validation {
    condition     = alltrue([for v in values(var.feature_engineering_task_cpu) : contains([256, 512, 1024, 2048, 4096, 8192, 16384], v)])
    error_message = "Every feature_engineering_task_cpu value must be one of Fargate's supported CPU units: 256, 512, 1024, 2048, 4096, 8192, 16384."
  }
}

variable "feature_engineering_task_memory_per_vcpu_mib" {
  description = "Fargate memory (MiB) provisioned per feature_engineering_task_cpu vCPU, keyed by sport."
  type        = map(number)
  default = {
    nfl     = 4096
    ncaafb  = 4096
    nba     = 4096
    ncaambb = 7680
    pga     = 4096
    f1      = 4096
  }
  nullable = false
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

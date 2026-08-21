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
  default     = "https://site.web.api.espn.com/apis/site/v2/sports"
  nullable    = false
}

variable "espn_core_api_root_url" {
  description = "Root URL of ESPN's other, undocumented 'core' API (sports.core.api.espn.com), used for coach and injury enrichment -- see library/http/espn_core.py. Override via TF_VAR_espn_core_api_root_url from a GitHub Actions variable if ESPN's domain changes."
  type        = string
  default     = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl"
  nullable    = false
}

# Same core API/host as espn_core_api_root_url above, but basketball's own
# league path -- that variable's default is hardcoded to NFL's path
# (despite the generic name), so NCAA MBB's AP-poll client
# (library/http/ncaambb_core.py) needs its own sport-scoped variable
# rather than sharing it.
variable "ncaambb_espn_core_api_root_url" {
  description = "Root URL of ESPN's 'core' API (sports.core.api.espn.com), scoped to men's college basketball -- used for AP Top 25 poll history (see library/http/ncaambb_core.py). Override via TF_VAR_ncaambb_espn_core_api_root_url if ESPN's domain changes."
  type        = string
  default     = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/mens-college-basketball"
  nullable    = false
}

variable "espn_user_agent" {
  description = "User-Agent sent on every ESPN request. site.api.espn.com started 403ing plain scoreboard requests; site.web.api.espn.com + a non-browser UA is the confirmed-working combo."
  type        = string
  default     = "python-requests/2.31.0"
  nullable    = false
}

variable "cfbd_api_root_url" {
  description = "Root URL of CollegeFootballData.com's REST API v2, used by every NCAAFB task that calls it. No sport path suffix to append -- CFBD is football-only, unlike the shared ESPN root URLs above."
  type        = string
  default     = "https://api.collegefootballdata.com"
  nullable    = false
}

variable "third_party_api_key_secret_arn" {
  description = "ARN of the single shared Secrets Manager secret holding every sport's third-party API key as a JSON field (e.g. ncaa_fb_ingest_key, ncaa_fb_backfill_key) -- supplied via TF_VAR_third_party_api_key_secret_arn from the THIRD_PARTY_API_KEYS_SECRET_ARN GitHub Actions secret. Only the ARN is a Terraform input; the key material itself is resolved from Secrets Manager at cold start by library/http/cfbd.py, never appearing in state or CI logs."
  type        = string
  nullable    = false
}

# ── Training compute budget ──────────────────────────────────────────────────
# Drives local.training_max_concurrency (locals-training-compute.tf), which
# sets TrainAllTargets' MaxConcurrency in sfn-training-orchestrator.tf and
# the cpu/memory on every nfl-train-*-model ECS task definition.

variable "fargate_account_vcpu_limit" {
  description = "Account-wide Fargate on-demand concurrent vCPU quota (as shown in the Service Quotas console for 'Fargate On-Demand vCPU count'; CI supplies the real value via FARGATE_VCPU_LIMIT, tf_install.yml). Consulted for both feature-engineering (local.feature_engineering_max_concurrency, locals-feature-engineering-compute.tf) and training's own on-demand fallback share -- RunTrainingTask itself launches on FARGATE_SPOT (see fargate_spot_account_vcpu_limit below), but its Catch can fall through to RunTrainingTaskOnDemand, on-demand, within that same concurrent slot"
  type        = number
  default     = 250
  nullable    = false
}

variable "fargate_spot_account_vcpu_limit" {
  description = "Account-wide Fargate SPOT concurrent vCPU quota (Service Quotas console, 'Fargate Spot vCPU count'; CI supplies the real value via FARGATE_SPOT_VCPU_LIMIT, tf_install.yml) -- a separate quota from fargate_account_vcpu_limit's on-demand one, and the one that actually constrains RunTrainingTask's CapacityProviderStrategy. Raise this default only once a Service Quotas increase is actually granted, not before, so local.training_max_concurrency never asks ECS RunTask for more concurrent Spot tasks than the account can actually satisfy"
  type        = number
  default     = 64
  nullable    = false
}

variable "training_vcpu_budget_fraction" {
  description = "Max fraction of both fargate_account_vcpu_limit and fargate_spot_account_vcpu_limit the training orchestrator's concurrent ECS tasks may consume at once (CI supplies the real value via VCPU_BUDGET_FRACTION, tf_install.yml). On the on-demand side this leaves the rest of the quota free for feature-engineering, backfill, and ingest tasks that can run at the same time; on the Spot side, nothing else in this project shares that quota, so the fraction instead lowers target concurrency to reduce how often a Spot reclaim interrupts an in-flight training candidate"
  type        = number
  default     = 0.75 # 3/4
  nullable    = false

  validation {
    condition     = var.training_vcpu_budget_fraction > 0 && var.training_vcpu_budget_fraction <= 1
    error_message = "training_vcpu_budget_fraction must be between 0 (exclusive) and 1 (inclusive)."
  }
}

variable "training_task_vcpu" {
  description = "vCPU allocated to each concurrent training ECS task (win-probability/score/player-prop). Must be a value Fargate actually supports; lowering it raises local.training_max_concurrency for the same vCPU budget"
  type        = number
  default     = 16
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

# ── Feature-engineering compute ──────────────────────────────────────────────
# Per-sport cpu/memory for each sport's standalone feature-engineering
# Fargate task (ecs-task-<sport>-feature-engineering.tf). Each sport's task
# runs alone, holding its full history in memory at once, and that
# history's size varies by sport. No fallback default, so a missed entry
# fails the apply instead of silently under-provisioning a new sport's task.

variable "feature_engineering_task_cpu" {
  description = "Fargate CPU units (1024 per vCPU) for each sport's feature-engineering task, keyed by sport -- left at NFL's original size since nothing has ever indicated NFL's task is CPU-bound; only bump a sport's entry here if evidence (throttling, a run that's slow rather than OOM-killed) actually points at CPU, not just because memory needed raising"
  type        = map(number)
  default = {
    nfl    = 1024
    ncaafb = 8192
    nba    = 8192
  }
  nullable = false

  validation {
    condition     = alltrue([for v in values(var.feature_engineering_task_cpu) : contains([256, 512, 1024, 2048, 4096, 8192, 16384], v)])
    error_message = "Every feature_engineering_task_cpu value must be one of Fargate's supported CPU units: 256, 512, 1024, 2048, 4096, 8192, 16384."
  }
}

variable "feature_engineering_task_memory_per_vcpu_mib" {
  description = "Fargate memory (MiB) provisioned per feature_engineering_task_cpu vCPU, keyed by sport -- local.feature_engineering_task_memory (locals-feature-engineering-compute.tf) multiplies this by each sport's own vCPU count, same derive-from-vCPU pattern training_task_memory_per_vcpu_mib uses. ncaafb's real 10-season FBS backfill was OOM-killed at 2048 total (Reason: OutOfMemoryError), so it's raised to 8192 -- the max memory Fargate allows at 1 vCPU -- rather than raising cpu, since nothing pointed at CPU being the problem (see feature_engineering_task_cpu's own description). nfl is left unchanged since it has never shown a memory problem"
  type        = map(number)
  default = {
    nfl    = 4096
    ncaafb = 4096
    nba    = 4096
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


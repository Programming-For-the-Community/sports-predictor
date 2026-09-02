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

variable "jolpica_api_root_url" {
  description = "Root URL of Jolpica-F1 (the Ergast-compatible successor API), used by every F1 task that calls it. https, not http -- a plain http:// request 301-redirects to https (Cloudflare-enforced, confirmed live 2026-08-31); pointing straight at https avoids both the extra redirect round-trip against Jolpica's own strict rate limit and leaving that first request open to being stripped/rewritten on-path. Override via TF_VAR_jolpica_api_root_url if Jolpica's domain changes -- see library/http/f1.py's own DEFAULT_JOLPICA_API_ROOT_URL, which this shadows the same way espn_api_root_url shadows library/http/espn.py's own default."
  type        = string
  default     = "https://api.jolpi.ca/ergast/f1"
  nullable    = false
}

variable "jolpica_user_agent" {
  description = "User-Agent sent on every Jolpica-F1 request -- Jolpica's own docs require a real, non-default identifying User-Agent (unlike ESPN, which just needs to not look like a bot). See library/http/f1.py's own DEFAULT_JOLPICA_USER_AGENT."
  type        = string
  default     = "sports-predictor-f1-client/1.0 (personal-use sports analytics; non-commercial)"
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
  description = "Account-wide Fargate on-demand concurrent vCPU quota (as shown in the Service Quotas console for 'Fargate On-Demand vCPU count'; CI supplies the real value via FARGATE_VCPU_LIMIT, tf_install.yml). Consulted by feature-engineering (local.feature_engineering_max_concurrency, locals-feature-engineering-compute.tf), which is the only thing still running on Fargate for the training pipeline -- training itself moved fully to EC2 (ec2-training-asg.tf), so this quota is no longer shared with training's own on-demand fallback"
  type        = number
  default     = 250
  nullable    = false
}

variable "training_vcpu_budget_fraction" {
  description = "Max fraction of ec2_spot_account_vcpu_limit the training orchestrator's concurrent Spot ECS tasks may consume at once (CI supplies the real value via VCPU_BUDGET_FRACTION, tf_install.yml). Nothing else in this project shares that quota, so the fraction lowers target concurrency to reduce how often a Spot reclaim interrupts an in-flight training candidate"
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

# ── EC2 training compute (locals-training-compute.tf) ────────────────────────
# EC2 is the sole training compute path (ec2-training-asg.tf, sfn-training-
# orchestrator.tf) -- Fargate training was retired once EC2 held up on a
# real canary run. Only the Spot quota is tracked here -- the on-demand
# fallback path is deliberately small and fixed (ec2-training-asg.tf's own
# on-demand ASG hardcodes max_size = 1, independent of any account quota),
# not something local.training_vcpu_budget needs to share or be bounded by.

variable "ec2_spot_account_vcpu_limit" {
  description = "Account-wide EC2 Spot Standard-instance-family concurrent vCPU quota (Service Quotas console, 'All Standard (A, C, D, H, I, M, R, T, Z) Spot Instance Requests'; CI supplies the real value via EC2_SPOT_VCPU_LIMIT, tf_install.yml). Confirmed via this account's own real Service Quotas data (2026-09-02) and the AWS/Usage vCPU-denominated usage metric tied to it -- default reflects that real value, not a placeholder"
  type        = number
  default     = 256
  nullable    = false
}

variable "training_sport_concurrency" {
  description = "How many sports' ForEachSport iterations may run at once (sfn-training-orchestrator.tf) -- not hardcoded to 1, since EC2 vCPU quota is assumed unconstrained here (see the section comment above). Deliberately NOT capped by local.feature_engineering_max_concurrency (locals-feature-engineering-compute.tf, on-demand Fargate headroom) -- that's a real constraint on the Fargate compute RunFeatureEngineering itself uses, but TrainAllTargets (what this figure actually sizes) runs on a totally separate EC2 capacity pool, so the two concurrency limits are kept independent rather than one Fargate quota bounding EC2's own parallelism. Defaulted to 6 -- all 6 sports free to train concurrently, with no artificial cap of its own"
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
# Fargate task (ecs-task-<sport>-feature-engineering.tf). Each sport's task
# runs alone, holding its full history in memory at once, and that
# history's size varies by sport. No fallback default, so a missed entry
# fails the apply instead of silently under-provisioning a new sport's task.

variable "feature_engineering_task_cpu" {
  description = "Fargate CPU units (1024 per vCPU) for each sport's feature-engineering task, keyed by sport -- left at NFL's original size since nothing has ever indicated NFL's task is CPU-bound; only bump a sport's entry here if evidence (throttling, a run that's slow rather than OOM-killed) actually points at CPU, not just because memory needed raising. ncaambb starts at NBA's own 8192 (the max) -- D1's real per-season game volume is confirmed ~4x NBA's (see project-ncaambb-onboarding memory), so starting anywhere lower would just be guessing against already-known evidence, not the usual absence of it"
  type        = map(number)
  default = {
    nfl     = 1024
    ncaafb  = 8192
    nba     = 8192
    ncaambb = 8192
    # PGA's dataset is a single golfer-tournament Parquet file with no
    # player_game_stats/team_game_stats history to hold in memory at all
    # (design/DATA_SCHEMA.md -- a field-event sport has neither table),
    # and roughly one season's worth of NFL's own event volume spread
    # across ~9 backfilled seasons -- starts at NFL's own smallest size
    # on the same "no evidence yet points at needing more" basis, not a
    # guess.
    pga = 1024
    # Same reasoning as pga above -- F1's own datasets (driver_features/
    # constructor_features/sprint_features.parquet) have no player_game_
    # stats/team_game_stats history either, and 2010-present is a smaller
    # season count than PGA's own 2017-present window. No evidence yet
    # points at needing more than the smallest size.
    f1 = 1024
  }
  nullable = false

  validation {
    condition     = alltrue([for v in values(var.feature_engineering_task_cpu) : contains([256, 512, 1024, 2048, 4096, 8192, 16384], v)])
    error_message = "Every feature_engineering_task_cpu value must be one of Fargate's supported CPU units: 256, 512, 1024, 2048, 4096, 8192, 16384."
  }
}

variable "feature_engineering_task_memory_per_vcpu_mib" {
  description = "Fargate memory (MiB) provisioned per feature_engineering_task_cpu vCPU, keyed by sport -- local.feature_engineering_task_memory (locals-feature-engineering-compute.tf) multiplies this by each sport's own vCPU count, same derive-from-vCPU pattern training_task_memory_per_vcpu_mib uses. ncaafb's real 10-season FBS backfill was OOM-killed at 2048 total (Reason: OutOfMemoryError), so it's raised to 8192 -- the max memory Fargate allows at 1 vCPU -- rather than raising cpu, since nothing pointed at CPU being the problem (see feature_engineering_task_cpu's own description). nfl is left unchanged since it has never shown a memory problem. ncaambb starts at 7680 -- the highest per-vCPU value Fargate allows at 8 vCPU (total memory is capped at 61440 MiB there, unlike the 1-vCPU 8192 ceiling ncaafb/nba's own multiplier is keyed against), preemptively rather than waiting for a real OOM-kill -- same reasoning as its own feature_engineering_task_cpu entry: the ~4x-NBA volume is already-known evidence, not a guess this project's usual 'wait for a real signal' discipline would normally allow skipping"
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


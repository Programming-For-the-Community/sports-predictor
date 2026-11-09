# Terraform settings

Everything you can change about the deployed infrastructure, and where each
value comes from. A change to any of these takes effect on the next deploy
(App Deploy, or the infra job in `tf_install.yml`); none needs a code release
except the ones under **Code-level tunables**.

Sources:

- **GitHub secret / variable**: Settings › Secrets and variables › Actions.
  `tf_install.yml` passes it to Terraform as `TF_VAR_<name>`.
- **Optional override**: a GitHub variable passed to Terraform only when it
  holds a value (the "Assistant model overrides" step in `tf_install.yml`);
  unset or blank keeps the default in `Terraform/variables.tf`.
- **Default only**: set by its default in `Terraform/variables.tf`; change it
  there (or ask for a GitHub override to be wired in).

## 1. Set in GitHub (required)

Every one of these is mapped straight to Terraform, so it must exist: an unset
one reaches Terraform as an empty string and the plan fails (the workflow's
"Validate required variables" step also checks several by name).

| Terraform variable | GitHub | Kind | What it is |
|---|---|---|---|
| `account_id` | `AWS_ACCOUNT_ID` | secret | AWS account ID |
| `alert_email` | `ALERT_EMAIL` | secret | Receives AWS Budgets alerts |
| `vpc_id` | `VPC_ID` | secret | VPC to deploy into |
| `vpc_cidr` | `VPC_CIDR` | secret | The VPC's CIDR, for security-group rules |
| `domain_name` | `DOMAIN_NAME` | secret | Root domain in Route 53 |
| `hosted_zone_id` | `DOMAIN_HOSTED_ZONE_ID` | secret | Route 53 hosted zone for the domain |
| `ecr_repo_url` | `ECR_URI` | secret | Shared ECR repository URL |
| `third_party_api_key_secret_arn` | `THIRD_PARTY_API_KEYS_SECRET_ARN` | secret | Secrets Manager secret with the third-party API keys |
| `region` | `AWS_RESOURCE_REGION` | variable | AWS region (us-east-2) |
| `project` | `PROJECT` | variable | Project name, used in every resource name |
| `owner` | `OWNER` | variable | Owner tag |
| `private1_subnet_cidr` / `private2_…` / `private3_…` | `SUBNET_A_CIDR` / `SUBNET_B_CIDR` / `SUBNET_C_CIDR` | variable | Private subnets |
| `public1_subnet_cidr` / `public2_…` / `public3_…` | `PUBLIC1_SUBNET_CIDR` / `PUBLIC2_…` / `PUBLIC3_…` | variable | Public subnets |
| `espn_api_root_url` | `ESPN_API_ROOT_URL` | variable | ESPN site API |
| `espn_core_api_root_url` | `ESPN_CORE_API_ROOT_URL` | variable | ESPN core API (NFL) |
| `ncaambb_espn_core_api_root_url` | `NCAAMBB_ESPN_CORE_API_ROOT_URL` | variable | ESPN core API (men's college basketball) |
| `cfbd_api_root_url` | `CFBD_API_ROOT_URL` | variable | CollegeFootballData API |
| `jolpica_api_root_url` | `JOLPICA_API_ROOT_URL` | variable | Jolpica-F1 API |
| `fargate_account_vcpu_limit` | `FARGATE_VCPU_LIMIT` | variable | Account Fargate vCPU quota |
| `training_vcpu_budget_fraction` | `VCPU_BUDGET_FRACTION` | variable | Share of the Spot quota training may use |
| `training_task_vcpu` | `TRAINING_TASK_VCPU_LIMIT` | variable | vCPU per training task |
| `ec2_spot_account_vcpu_limit` | `EC2_SPOT_VCPU_LIMIT` | variable | Account Spot vCPU quota |
| `training_sport_concurrency` | `TRAINING_SPORT_CONCURRENCY` | variable | Sports trained at once |
| `environment` | — | workflow input | The deploy's `environment` input, else `development` |

## 2. Optional overrides

| Terraform variable | GitHub variable | Default | Allowed |
|---|---|---|---|
| `assistant_model` | `ASSISTANT_MODEL` | `us.anthropic.claude-haiku-4-5-20251001-v1:0` | A Bedrock model ID from a US-based creator (anthropic, openai, nvidia, meta, amazon, google), with or without the `us.` profile prefix. **Currently set to `nvidia.nemotron-super-3-120b`.** |
| `assistant_effort` | `ASSISTANT_EFFORT` | `none` | `none`, `low`, `medium`, `high` (Claude models that take effort only) |
| `assistant_temperature` | `ASSISTANT_TEMPERATURE` | `0.2` | 0 to 1; non-Claude models only |
| `assistant_judge_model` | `ASSISTANT_JUDGE_MODEL` | `us.anthropic.claude-haiku-4-5-20251001-v1:0` | Same rule as `assistant_model`; the evaluation judge |
| `assistant_daily_limit_public` | `ASSISTANT_DAILY_LIMIT_PUBLIC` | `50` | Questions per user per day, 1 to 1000 |
| `assistant_daily_limit_admin` | `ASSISTANT_DAILY_LIMIT_ADMIN` | `300` | Questions per admin per day, 1 to 5000 |
| `assistant_evaluation_sampling_percentage` | `ASSISTANT_EVALUATION_SAMPLING_PERCENTAGE` | `10` | Share of sessions scored, 0.01 to 100 (~$0.07 per scored turn) |
| `monthly_limit` | `MONTHLY_LIMIT` | `30` | Whole-project monthly budget (USD) |

## 3. Default only (edit `Terraform/variables.tf`)

| Terraform variable | Default | What it controls |
|---|---|---|
| `per_sport_limits` | `{}` | Per-sport monthly budgets |
| `waf_rate_limit_per_ip` | `1000` | Requests per IP per 5 minutes through CloudFront |
| `assistant_rate_limit_per_ip` | `60` | Requests per IP per 5 minutes to the assistant |
| `mobile_refresh_token_days` | `3650` | Outer limit on an Android sign-in; the app signs out after 30 days unopened |
| `espn_user_agent` | `python-requests/2.31.0` | User-Agent on ESPN requests |
| `jolpica_user_agent` | `sports-predictor-f1-client/1.0 …` | User-Agent on Jolpica requests |
| `training_task_memory_per_vcpu_mib` | `4096` | Training task memory per vCPU |
| `feature_engineering_task_cpu` | per-sport map | Feature-engineering task CPU |
| `feature_engineering_task_memory_per_vcpu_mib` | per-sport map | Feature-engineering memory per vCPU |

## 4. Code-level tunables (Terraform locals)

Not variables: changing one means editing the file and deploying. Listed so
you know they exist.

| Setting | File | Current |
|---|---|---|
| Model max tokens | `agentcore-assistant.tf` (`assistant_settings`) | 4096 |
| Steps per answer / calls per step / calls run at once | same | 4 / 10 / 4 |
| Tool Lambdas' reserved concurrency (public / admin) | `lambda-assistant-tools.tf` | 20 / 5 |
| Max question length / history turns / session turns | same | 1000 chars / 10 / 30 |
| Rows per tool list / size cap per tool result | `agentcore-assistant.tf` (`assistant_tool_settings`) | 100 / 80,000 chars |
| Trend points / hourly-trend days | same | 400 / 7 |
| Repeat-offender thresholds | same | 100 blocks or 3 days |
| Contextual grounding (public guardrail) | `agentcore-assistant.tf` (`assistant_public_grounding`) | off |
| Guardrail topics and filters | `bedrock-guardrails-assistant.tf` | unrelated-tasks, infrastructure; content, prompt-attack, profanity |
| Evaluators | `agentcore-evaluations.tf` | Faithfulness, Helpfulness, Tool selection accuracy, public-scope judge |
| Cost producer: restated days / backfill months / month re-pull day | `lambda-assistant-producers.tf` | 3 / 12 / 5th |
| Traffic producer lookback | same | 2 hours |
| Producer schedules | `scheduler-assistant-producers.tf` | cost daily 14:00 UTC, traffic hourly at :10 |

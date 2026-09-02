# Tagging Strategy

A consistent tagging convention applied at resource creation, so AWS Billing/Cost Explorer can break down spend by sport and by pipeline stage without manual bookkeeping.

## Tag keys

| Tag key | Allowed values | Purpose |
|---|---|---|
| `Project` | `sports-predictor` | Constant across every resource — isolates this project's spend from anything else in the account. |
| `Owner` | `var.owner` | Set once from a Terraform variable on every resource via `local.common_tags`, alongside `Project`/`Environment` — not sport- or component-specific, so it isn't part of the per-resource tagging discipline below. |
| `Sport` | `nfl`, `ncaafb`, `nba`, `ncaambb`, `pga`, `f1`, `shared` | Which sport a resource belongs to. Use `shared` for infrastructure that serves all sports (API Gateway, Cognito, the registry table, the frontend). No hyphens — matches the sport key used throughout DynamoDB/S3 (`design/DATA_SCHEMA.md`), not a separate naming scheme. |
| `Component` | `ingestion`, `storage`, `training`, `serving`, `frontend`, `orchestration`, `billing`, `networking`, `observability` | Which pipeline stage a resource belongs to, independent of sport. `orchestration` covers EventBridge/Step Functions resources that trigger other components rather than doing the work themselves. `billing` covers cost-management resources. `networking` covers VPC, subnet, route table, and endpoint resources that underlie all other components. `observability` covers CloudWatch dashboards/alarms, SNS, and X-Ray tracing config. |
| `Environment` | `prod`, `dev` | Worth keeping even for a personal project — useful the moment you spin up a second stack to test a schema change without touching live data. |

Five tags is intentionally minimal. Resist adding more unless you hit a specific reporting need — every tag is something you have to remember to set correctly on every new resource, including the ones added later by people (or Claude Code sessions) who didn't see this document.

## Applying tags

Apply tags through whatever infrastructure-as-code tool you use (CDK or Terraform), at the stack or construct level, so every resource a stack creates inherits the same tags automatically. Tagging manually after the fact is how tagging coverage quietly rots — a resource added in a hotfix six months from now is easy to forget, and at that point cost in Cost Explorer just shows up as "untagged."

Not every AWS resource supports tags at the same granularity — S3 buckets and DynamoDB tables can be tagged directly, but individual S3 objects and DynamoDB items cannot carry their own cost-allocation tags. That's fine: bucket-level and table-level tagging is the right granularity here, since cost tracking at the per-object level isn't meaningful for this project anyway (you care about "what is the PGA pipeline costing," not "what does this one S3 object cost").

## Activating cost allocation tags

Tags don't appear in Cost Explorer automatically — they have to be activated once:

1. Go to **Billing and Cost Management → Cost Allocation Tags** in the console.
2. Find `Project`, `Sport`, `Component`, and `Environment` under **User-Defined Cost Allocation Tags**.
3. Select each and click **Activate**.
4. Allow up to 24 hours for activated tags to start appearing in Cost Explorer reports — tagged resources created before activation will backfill once it takes effect, but there can be a short lag.

## Using tags once activated

In **Cost Explorer**, group by the `Sport` tag to see what each sport's pipeline actually costs — this is the most useful single view once you've onboarded a few sports, since it will tell you directly whether, say, NCAA MBB's higher game volume is meaningfully more expensive than NFL's. Group by `Component` to see whether cost is concentrated in ingestion, training, or serving, which is the more useful view while you're still building Phase 0–3 and most resources are tagged `shared` or belong to a single sport.

In **AWS Budgets** (`Terraform/budgets.tf`, IaC-managed), a budget is already scoped both account-wide and per-sport via the `Sport` tag — `aws_budgets_budget.project` for the whole account, `aws_budgets_budget.per_sport` filtered to each `Sport` value, both alerting at 80%/100% actual spend and 100% forecasted spend to the email in `var.alert_email`.

## Native cost dashboards (manual, console-only)

**Billing and Cost Management → Dashboards** supports pinning a tag-grouped cost report directly — group by `Sport` or `Component` the same way you would in Cost Explorer, but pinned to a dashboard instead of rebuilt each visit. There's no Terraform resource for this (the `hashicorp/aws` provider manages `aws_budgets_budget` and cost-allocation-tag activation, not the Dashboards UI itself), so this is a one-time manual setup, same as tag activation above:

1. Confirm the cost allocation tags are Active first (see the activation steps above) — a dashboard widget grouped by a tag that has no cost data yet will show empty/no groupings.
2. Go to **Billing and Cost Management → Dashboards**.
3. Add a widget grouped by the `Sport` tag, and a second grouped by `Component`.
4. Re-check after a day — like Cost Explorer, newly tagged resources can take up to 24 hours to show up in grouped reports.

This tag-grouped cost view is intentionally separate from the 7 operational CloudWatch dashboards (`docs/AWS_ARCHITECTURE.md`'s observability diagram) — those cover request volume/errors/latency/throttling, not spend; CloudWatch billing metrics can't group by tag at all, so cost visibility stays in Cost Explorer/Budgets/the console Dashboards tab above, not a CloudWatch dashboard.

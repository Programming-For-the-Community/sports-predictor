# Tagging Strategy

A consistent tagging convention applied at resource creation, so AWS Billing/Cost Explorer can break down spend by sport and by pipeline stage without manual bookkeeping.

## Tag keys

| Tag key | Allowed values | Purpose |
|---|---|---|
| `Project` | `sports-predictor` | Constant across every resource — isolates this project's spend from anything else in the account. |
| `Sport` | `nfl`, `ncaa-fb`, `nba`, `ncaa-mbb`, `pga`, `f1`, `shared` | Which sport a resource belongs to. Use `shared` for infrastructure that serves all sports (API Gateway, Cognito, the registry table, the frontend). |
| `Component` | `ingestion`, `storage`, `training`, `serving`, `frontend` | Which pipeline stage a resource belongs to, independent of sport. |
| `Environment` | `prod`, `dev` | Worth keeping even for a personal project — useful the moment you spin up a second stack to test a schema change without touching live data. |

Four tags is intentionally minimal. Resist adding more unless you hit a specific reporting need — every tag is something you have to remember to set correctly on every new resource, including the ones added later by people (or Claude Code sessions) who didn't see this document.

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

In **AWS Budgets**, you can scope a budget to a specific tag value rather than the whole account — for example, a budget filtered to `Sport=pga` that alerts if that one sport's pipeline starts costing more than expected, separate from the account-wide budget recommended in `PROJECT_PLAN.md`. This is worth setting up once you have more than two or three sports onboarded, since by then "something costs more than expected" is genuinely more useful to localize to a sport than to the whole account.

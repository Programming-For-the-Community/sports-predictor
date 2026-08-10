# How much on-demand Fargate vCPU headroom is actually available for
# feature-engineering tasks (RunFeatureEngineering, sfn-training-
# orchestrator.tf launches these on-demand -- see that file's own comment
# on why training moved to FARGATE_SPOT but feature-engineering didn't)
# once training's own on-demand fallback share (local.training_vcpu_budget's
# on-demand term, locals-training-compute.tf) is set aside.
#
# Informational only for now -- ForEachSport's own MaxConcurrency
# (sfn-training-orchestrator.tf) stays pinned at 1, so at most one sport's
# feature-engineering task ever runs at once regardless of this number.
# Letting multiple sports run concurrently is a separate, not-yet-made
# change: it would also mean dividing training_vcpu_budget across however
# many sports could realistically run at once, not just exposing this
# figure. Kept here so the real on-demand headroom this project has is
# visible and ready to use if that change is ever made -- not accounted
# for against backfill or ingest, same as training_vcpu_budget's own
# reasoning: those aren't part of this budget math either.
locals {
  feature_engineering_vcpu_budget = var.fargate_account_vcpu_limit - floor(var.fargate_account_vcpu_limit * var.training_vcpu_budget_fraction)

  # feature_engineering_task_cpu (variables.tf) is keyed by sport and can
  # differ per sport -- take the largest configured size so this stays a
  # safe worst-case even if a future sport's task ends up bigger than
  # today's 1 vCPU (nfl and ncaafb are both 1024 currently).
  feature_engineering_max_task_vcpu = max([for cpu in values(var.feature_engineering_task_cpu) : cpu]...) / 1024

  feature_engineering_max_concurrency = max(1, floor(local.feature_engineering_vcpu_budget / local.feature_engineering_max_task_vcpu))

  # Each sport's own task memory (MiB), derived from ITS OWN vCPU count --
  # feature_engineering_task_cpu[sport] / 1024 vCPU times
  # feature_engineering_task_memory_per_vcpu_mib[sport] -- same
  # derive-from-vCPU pattern locals-training-compute.tf's
  # training_task_memory uses, kept per-sport (rather than one shared
  # ratio) since how much memory a sport's full history needs per vCPU
  # varies by sport (see feature_engineering_task_memory_per_vcpu_mib's
  # own description). ecs-task-<sport>-feature-engineering.tf reads this,
  # not the raw per-vCPU variable directly.
  feature_engineering_task_memory = {
    for sport, cpu in var.feature_engineering_task_cpu :
    sport => (cpu / 1024) * var.feature_engineering_task_memory_per_vcpu_mib[sport]
  }
}

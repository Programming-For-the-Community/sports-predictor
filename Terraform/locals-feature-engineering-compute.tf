# Available on-demand Fargate vCPU headroom for feature-engineering tasks,
# once training's own on-demand fallback share (local.training_vcpu_budget,
# locals-training-compute.tf) is set aside.
#
# ForEachSport's own MaxConcurrency (sfn-training-orchestrator.tf) is
# pinned at 1, so at most one sport's feature-engineering task runs at
# once regardless of this value.
locals {
  feature_engineering_vcpu_budget = var.fargate_account_vcpu_limit - floor(var.fargate_account_vcpu_limit * var.training_vcpu_budget_fraction)

  # feature_engineering_task_cpu (variables.tf) is keyed by sport; take the
  # largest configured size so this stays a safe worst-case.
  feature_engineering_max_task_vcpu = max([for cpu in values(var.feature_engineering_task_cpu) : cpu]...) / 1024

  feature_engineering_max_concurrency = max(1, floor(local.feature_engineering_vcpu_budget / local.feature_engineering_max_task_vcpu))

  # Each sport's own task memory (MiB), derived from its own vCPU count:
  # feature_engineering_task_cpu[sport] / 1024 vCPU times
  # feature_engineering_task_memory_per_vcpu_mib[sport].
  feature_engineering_task_memory = {
    for sport, cpu in var.feature_engineering_task_cpu :
    sport => (cpu / 1024) * var.feature_engineering_task_memory_per_vcpu_mib[sport]
  }
}

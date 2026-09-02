# Available on-demand Fargate vCPU headroom for feature-engineering tasks.
# Training itself no longer shares this pool at all -- it moved fully to a
# separate EC2 vCPU budget (locals-training-compute.tf) once Fargate
# training was retired, so this local gets the full on-demand quota rather
# than reserving a share away from it.
#
# ForEachSport's own MaxConcurrency (sfn-training-orchestrator.tf) is NOT
# pinned to 1 -- up to local.training_sport_concurrency sports can run
# RunFeatureEngineering at once, so feature_engineering_max_concurrency
# below is a real, load-bearing cap on how many of those can actually get
# Fargate capacity simultaneously, not just a formality.
locals {
  feature_engineering_vcpu_budget = var.fargate_account_vcpu_limit

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

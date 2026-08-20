# Derives the training orchestrator's per-task Fargate sizing and its
# TrainAllTargets Map's MaxConcurrency (sfn-training-orchestrator.tf) from
# the four variables in variables.tf's "Training compute budget" section.
locals {
  # Total vCPU the training Step Function's concurrent ECS tasks may
  # consume at once -- the smaller of two independently-derived caps, each
  # holding back training_vcpu_budget_fraction of its own quota: the SPOT
  # quota RunTrainingTask launches on, and the ON-DEMAND quota its
  # RunTrainingTaskOnDemand fallback shares with feature-engineering/
  # backfill/ingest. floor(), not round(), on both terms so actual usage
  # can't exceed either cap.
  training_vcpu_budget = min(
    floor(var.fargate_spot_account_vcpu_limit * var.training_vcpu_budget_fraction),
    floor(var.fargate_account_vcpu_limit * var.training_vcpu_budget_fraction),
  )

  # How many training tasks, each sized at training_task_vcpu, fit in that
  # budget. max(1, ...) guarantees training never fully serializes to zero
  # concurrency if the budget math rounds down that far.
  training_max_concurrency = max(1, floor(local.training_vcpu_budget / var.training_task_vcpu))

  # Fargate task definitions take cpu/memory as strings, in CPU units
  # (1024 per vCPU) and MiB respectively.
  training_task_cpu    = tostring(var.training_task_vcpu * 1024)
  training_task_memory = tostring(var.training_task_vcpu * var.training_task_memory_per_vcpu_mib)
}

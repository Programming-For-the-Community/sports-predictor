# Derives the training orchestrator's per-task Fargate sizing and its
# TrainAllTargets Map's MaxConcurrency (sfn-training-orchestrator.tf) from
# the four variables in variables.tf's "Training compute budget" section --
# nothing below should ever need to change on its own; only those
# variables should.
locals {
  # Total vCPU the training Step Function's concurrent ECS tasks may
  # consume at once -- the smaller of two independently-derived caps, each
  # holding back training_vcpu_budget_fraction of its own quota:
  #
  #   - The SPOT quota. RunTrainingTask (sfn-training-orchestrator.tf)
  #     launches on FARGATE_SPOT -- nothing else in this project ever
  #     launches on Spot, so the fraction here isn't protecting a sibling
  #     consumer's headroom, it's a lower target concurrency to reduce how
  #     often a Spot reclaim interrupts an in-flight candidate.
  #   - The ON-DEMAND quota. This is the rare-case safety net: if every
  #     in-flight training task's Catch falls through to
  #     RunTrainingTaskOnDemand at once, that fallback launches on the
  #     SAME on-demand quota feature-engineering/backfill/ingest use --
  #     the fraction protects their headroom in that worst case.
  #
  # floor(), not round(), on both terms -- rounding up could let actual
  # usage exceed either cap.
  training_vcpu_budget = min(
    floor(var.fargate_spot_account_vcpu_limit * var.training_vcpu_budget_fraction),
    floor(var.fargate_account_vcpu_limit * var.training_vcpu_budget_fraction),
  )

  # How many training tasks, each sized at training_task_vcpu, fit in that
  # budget. max(1, ...) guarantees training never fully serializes to zero
  # concurrency if the budget math rounds down that far.
  training_max_concurrency = max(1, floor(local.training_vcpu_budget / var.training_task_vcpu))

  # Fargate task definitions take cpu/memory as strings, in CPU units
  # (1024 per vCPU) and MiB respectively -- shared by every
  # nfl-train-*-model task definition (ecs-task-nfl-train-*.tf) so they
  # stay sized consistently with the concurrency math above.
  training_task_cpu    = tostring(var.training_task_vcpu * 1024)
  training_task_memory = tostring(var.training_task_vcpu * var.training_task_memory_per_vcpu_mib)
}

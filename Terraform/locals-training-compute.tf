# Derives the training orchestrator's per-task Fargate sizing and its
# TrainAllTargets Map's MaxConcurrency (sfn-training-orchestrator.tf) from
# the four variables in variables.tf's "Training compute budget" section --
# nothing below should ever need to change on its own; only those
# variables should.
locals {
  # Total vCPU the training Step Function's concurrent ECS tasks may
  # consume at once. floor(), not round() -- rounding up could let actual
  # usage exceed fargate_account_vcpu_limit's own fraction.
  training_vcpu_budget = floor(var.fargate_account_vcpu_limit * var.training_vcpu_budget_fraction)

  # How many training tasks, each sized at training_task_vcpu, fit in that
  # budget. training_min_concurrent_tasks is a TARGET floor, not an
  # unconditional one -- min()'d against the budget-derived value below so
  # it can never push concurrency past training_vcpu_budget, which would
  # otherwise silently request more vCPU than the account (or the
  # headroom this fraction deliberately reserves for feature-engineering/
  # ingest/backfill tasks) actually has, and fail ECS RunTask calls once
  # real concurrency hit that ceiling. That's not hypothetical: at
  # training_task_vcpu=16 and a real 64-vCPU account quota, the raw
  # budget-derived value is only 2 -- training_min_concurrent_tasks'
  # default of 5 would have demanded 80 vCPU before this clamp existed.
  # max(1, ...) still guarantees training never fully serializes to zero
  # concurrency if the budget math rounds down that far.
  training_max_concurrency = max(
    1,
    min(
      var.training_min_concurrent_tasks,
      floor(local.training_vcpu_budget / var.training_task_vcpu),
    ),
  )

  # Fargate task definitions take cpu/memory as strings, in CPU units
  # (1024 per vCPU) and MiB respectively -- shared by every
  # nfl-train-*-model task definition (ecs-task-nfl-train-*.tf) so they
  # stay sized consistently with the concurrency math above.
  training_task_cpu    = tostring(var.training_task_vcpu * 1024)
  training_task_memory = tostring(var.training_task_vcpu * var.training_task_memory_per_vcpu_mib)
}

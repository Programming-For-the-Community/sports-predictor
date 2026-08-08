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
  # budget -- floor()'d so concurrent usage never exceeds
  # training_vcpu_budget, then floored at training_min_concurrent_tasks so
  # a shrunken budget or an oversized training_task_vcpu can't serialize
  # training down to one task at a time. Lowering training_task_vcpu (if
  # CloudWatch/Container Insights shows a training task isn't using all of
  # its allocated vCPU) raises this automatically, within the same budget.
  training_max_concurrency = max(
    var.training_min_concurrent_tasks,
    floor(local.training_vcpu_budget / var.training_task_vcpu),
  )

  # Fargate task definitions take cpu/memory as strings, in CPU units
  # (1024 per vCPU) and MiB respectively -- shared by every
  # nfl-train-*-model task definition (ecs-task-nfl-train-*.tf) so they
  # stay sized consistently with the concurrency math above.
  training_task_cpu    = tostring(var.training_task_vcpu * 1024)
  training_task_memory = tostring(var.training_task_vcpu * var.training_task_memory_per_vcpu_mib)
}

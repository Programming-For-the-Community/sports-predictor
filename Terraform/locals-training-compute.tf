# Training's own compute budget -- EC2 is the only training path.
# sfn-training-orchestrator.tf is the only consumer.
locals {
  # Spot's own quota -- the path RunTrainingTask actually uses. The
  # on-demand fallback is a deliberately small, fixed pool instead:
  # ec2-training-asg.tf's own on-demand ASG hardcodes max_size = 1,
  # independent of this budget.
  training_vcpu_budget = floor(var.ec2_spot_account_vcpu_limit * var.training_vcpu_budget_fraction)

  # Sports run concurrently, governed by var.training_sport_concurrency.
  training_sport_concurrency = var.training_sport_concurrency

  # Divides the vCPU budget by sport-concurrency before dividing by
  # per-task vCPU, so N sports' TrainAllTargets Maps running at once
  # never jointly request more than training_vcpu_budget total. This is
  # a per-sport figure -- sfn-training-orchestrator.tf's own
  # TrainAllTargets MaxConcurrency, one instance per in-flight target
  # within a single sport's own Map.
  training_max_concurrency = max(1, floor(local.training_vcpu_budget / local.training_sport_concurrency / var.training_task_vcpu))

  # The aggregate ceiling across every sport combined -- ec2-training-
  # asg.tf's Spot ASG is one fleet shared by all of them, so its own
  # max_size needs the total the budget can support, not one sport's
  # 1/N share.
  training_max_instances = max(1, floor(local.training_vcpu_budget / var.training_task_vcpu))

  # ECS task definitions take cpu/memory as strings, in CPU units (1024
  # per vCPU) and MiB respectively.
  training_task_cpu    = tostring(var.training_task_vcpu * 1024)
  training_task_memory = tostring(var.training_task_vcpu * var.training_task_memory_per_vcpu_mib)
}

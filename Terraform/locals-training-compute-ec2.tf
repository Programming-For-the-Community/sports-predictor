# EC2 training track's own compute budget -- parallel to
# locals-training-compute.tf's Fargate math, not a replacement for it.
# sfn-training-orchestrator-ec2.tf is the only consumer.
locals {
  # Same min()-of-two-quotas shape as local.training_vcpu_budget
  # (locals-training-compute.tf), against the EC2-specific quota
  # variables instead of the Fargate ones.
  ec2_training_vcpu_budget = min(
    floor(var.ec2_spot_account_vcpu_limit * var.training_vcpu_budget_fraction),
    floor(var.ec2_ondemand_account_vcpu_limit * var.training_vcpu_budget_fraction),
  )

  # Unlike the Fargate orchestrator (ForEachSport pinned to 1), sports can
  # run concurrently here -- governed only by var.training_ec2_sport_
  # concurrency itself, not by local.feature_engineering_max_concurrency
  # (locals-feature-engineering-compute.tf). That Fargate on-demand
  # headroom local is a real constraint on RunFeatureEngineering (which
  # stays on Fargate, unchanged, even within this EC2 orchestrator -- see
  # sfn-training-orchestrator-ec2.tf's own comment), but TrainAllTargets --
  # the piece this concurrency figure actually exists to size -- runs on a
  # completely separate EC2 capacity pool, so tying its concurrency to a
  # Fargate quota would bound one compute method's parallelism by an
  # unrelated one's.
  ec2_training_sport_concurrency = var.training_ec2_sport_concurrency

  # Divides the vCPU budget by sport-concurrency BEFORE dividing by
  # per-task vCPU, so N sports' TrainAllTargets Maps running at once can
  # never jointly request more than ec2_training_vcpu_budget total --
  # each sport's own Map only ever asks for its 1/N share.
  ec2_training_target_concurrency = max(1, floor(local.ec2_training_vcpu_budget / local.ec2_training_sport_concurrency / var.training_task_vcpu))
}

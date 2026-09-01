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
  # run concurrently here. local.feature_engineering_max_concurrency
  # (locals-feature-engineering-compute.tf) already computes the on-demand
  # Fargate headroom RunFeatureEngineering itself needs -- both tracks
  # share that same task, so this can't exceed it regardless of how large
  # var.training_ec2_sport_concurrency is set.
  ec2_training_sport_concurrency = min(var.training_ec2_sport_concurrency, local.feature_engineering_max_concurrency)

  # Divides the vCPU budget by sport-concurrency BEFORE dividing by
  # per-task vCPU, so N sports' TrainAllTargets Maps running at once can
  # never jointly request more than ec2_training_vcpu_budget total --
  # each sport's own Map only ever asks for its 1/N share.
  ec2_training_target_concurrency = max(1, floor(local.ec2_training_vcpu_budget / local.ec2_training_sport_concurrency / var.training_task_vcpu))
}

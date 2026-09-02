# Training's own compute budget -- EC2 is the only training path (Fargate
# training was retired once the EC2 track's real cost/reliability held up
# under test). sfn-training-orchestrator.tf is the only consumer.
locals {
  # Spot's own quota only -- Spot is the path this figure actually governs
  # (RunTrainingTask, sfn-training-orchestrator.tf). On-demand is a rare,
  # deliberately small fallback for a single stuck target, not a pool this
  # budget needs to share: ec2-training-asg.tf's own on-demand ASG already
  # hardcodes max_size = 1, completely independent of
  # ec2_ondemand_account_vcpu_limit (a real EC2 account quota, ~64 vCPU,
  # but never the on-demand path's actual ceiling here -- 1 instance is).
  # min()-ing this budget against that account quota (its earlier form)
  # throttled the common Spot-path concurrency down to a number that had
  # nothing to do with the real, much smaller on-demand fallback capacity.
  training_vcpu_budget = floor(var.ec2_spot_account_vcpu_limit * var.training_vcpu_budget_fraction)

  # Sports run concurrently (not pinned to 1) -- governed only by
  # var.training_sport_concurrency itself, not by local.feature_engineering_
  # max_concurrency (locals-feature-engineering-compute.tf). That Fargate
  # on-demand headroom local is a real constraint on RunFeatureEngineering
  # (which stays on Fargate on-demand, unchanged -- see sfn-training-
  # orchestrator.tf's own comment), but TrainAllTargets -- the piece this
  # concurrency figure actually exists to size -- runs on a completely
  # separate EC2 capacity pool, so tying its concurrency to a Fargate quota
  # would bound one compute method's parallelism by an unrelated one's.
  training_sport_concurrency = var.training_sport_concurrency

  # Divides the vCPU budget by sport-concurrency BEFORE dividing by
  # per-task vCPU, so N sports' TrainAllTargets Maps running at once can
  # never jointly request more than training_vcpu_budget total -- each
  # sport's own Map only ever asks for its 1/N share. This is a PER-SPORT
  # figure -- sfn-training-orchestrator.tf's own TrainAllTargets
  # MaxConcurrency, one instance per in-flight target within a single
  # sport's own Map.
  training_max_concurrency = max(1, floor(local.training_vcpu_budget / local.training_sport_concurrency / var.training_task_vcpu))

  # The AGGREGATE ceiling across every sport combined -- ec2-training-
  # asg.tf's Spot ASG is one fleet shared by all of them at once, so its
  # own max_size needs the total the budget can support, not one sport's
  # own 1/N share (training_max_concurrency above). Approximately
  # training_max_concurrency x training_sport_concurrency, but derived
  # straight from the budget instead of multiplying the two back together,
  # so flooring only happens once.
  training_max_instances = max(1, floor(local.training_vcpu_budget / var.training_task_vcpu))
}

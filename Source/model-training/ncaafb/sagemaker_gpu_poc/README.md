# NCAAFB player-prop GPU training benchmark (one-off POC)

Not part of the regular pipeline. Nothing here is invoked by Terraform,
Step Functions, or CI -- this exists purely so you can manually launch
**one** SageMaker Training Job and get a real wall-clock number for
`TARGET_STAT=receiving_touchdowns` (today's slowest NCAAFB player-prop
target at ~43 min on Fargate), before deciding whether a real SageMaker
migration is worth the engineering effort.

## Read this before you run it

`train_player_prop_model.py` doesn't train one model -- it runs a
tournament between **four** candidates (`library/ml/model_types.py`):
`XGBoostRegressorAdapter`, `ElasticNetAdapter`, `RandomForestRegressorAdapter`,
`MLPRegressorAdapter`. Only XGBoost has a GPU path. The other three are
plain CPU scikit-learn and will run at exactly the same speed on this
GPU instance as they do today -- a CloudWatch log from a real run showed
a single RandomForest CV fold taking **10.4 minutes** on its own
(`model__n_estimators=500, model__max_depth=10`), which may well be a
bigger piece of the 43-minute total than XGBoost's own 3,200-fit search
(`_XGB_SEARCH_ITERATIONS=400 * _XGB_CV_SPLITS=8`) is.

That's why `invoke.py` defaults to `ml.g4dn.4xlarge` (16 vCPU + 1 T4
GPU), not the cheaper `ml.g4dn.xlarge` (4 vCPU) -- matching today's
Fargate `training_task_vcpu` (16) so RandomForest/ElasticNet/MLP aren't
handicapped by fewer cores than production. If the total wall-clock time
doesn't drop much, that's real signal that this workload's bottleneck
isn't XGBoost, not that GPU acceleration itself failed.

## How it works

- `benchmark_entry_point.py` -- the SageMaker entry point. Installs a
  pinned pandas/pyarrow/scikit-learn/scikit-learn-intelex/xgboost stack
  matching `model-training/ncaafb/requirements.txt` (xgboost>=2.0 is
  required for the `device=` API `library/ml/model_types.py`'s GPU path
  uses -- not guaranteed to be what SageMaker's built-in XGBoost
  container bundles by default), then imports and runs the real,
  unmodified `train_player_prop_model.main()`.
- `invoke.py` -- launches the actual SageMaker Training Job. Sets
  `XGBOOST_DEVICE=cuda` in the job's environment, which
  `model_types.py`'s `_xgb_estimator_kwargs()`/`_xgb_search_n_jobs()`
  read: XGBoost trains with `tree_method="hist", device="cuda"`, and the
  outer `RandomizedSearchCV`'s `n_jobs` drops from -1 to 1 (a single GPU
  can't be fanned out across parallel search workers the way CPU cores
  can -- see that file's own comment).

Nothing about `train_player_prop_model.py` or the S3 read/write paths
changes -- it reads `ncaafb/training-data/player_features.parquet` and
writes model artifacts under `ncaafb/player-prop-receiving-touchdowns/`
in the same model-artifacts bucket production uses, via the role this
directory's Terraform (`Terraform/iam-sagemaker-gpu-poc.tf`) creates.

## Running it

```bash
cd Source/model-training/ncaafb/sagemaker_gpu_poc
pip install sagemaker boto3

python invoke.py \
    --role-arn "$(terraform -chdir=../../../../Terraform output -raw sagemaker_gpu_poc_role_arn)" \
    --bucket "$(terraform -chdir=../../../../Terraform output -raw model_artifacts_bucket)"
```

`invoke.py --help` for the rest of the flags (`--target-stat`,
`--instance-type`, `--region`). It blocks and streams logs until the job
finishes, then prints the billable training time.

**This has not been run against real AWS** -- built and reasoned through,
but not dry-run-verified end to end (no AWS access in the environment
this was written in). Expect to possibly debug a SageMaker SDK/container
detail on the first real invocation.

## Cost

`ml.g4dn.4xlarge` SageMaker training is roughly **$1.50-2.00/hr** in
us-east-2 (estimate -- confirm in the console before running; SageMaker
carries a markup over raw EC2 pricing that varies). A single ~30-45 min
run costs on the order of **$0.75-$1.50** -- trivial for one benchmark.
The real cost question is only relevant if this leads to standing
infrastructure, not this one-off run.

## Cleanup

This directory's Terraform (the `aws_iam_role.sagemaker_gpu_poc` role)
is safe to leave in place -- it can't do anything on its own, nothing
assumes it automatically. Remove it (and this directory) if you decide
not to pursue SageMaker further.

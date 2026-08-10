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

This ships as its own Docker image (own `Dockerfile` in this directory)
rather than the SageMaker XGBoost framework container + script-mode
combo `invoke.py`/`benchmark_entry_point.py` originally used. Reason:
that route required installing the `sagemaker` Python SDK to launch the
job, and recent SDK releases pull in `torch`/`onnxruntime`/`mlflow`/a
stack of `nvidia-cuda-*` wheels (local-mode training support, several GB)
that have nothing to do with actually submitting a training job --
enough to blow past AWS CloudShell's persistent storage limit. Building
and pushing an image sidesteps needing the SDK (or any local Python
environment) at all: build happens on a GitHub Actions runner, and the
job itself is launched by pointing the SageMaker console at the pushed
image directly.

- `Dockerfile` -- same build shape as `model-training/ncaafb/Dockerfile`
  (library/ + requirements.txt into a deps layer), with `ENTRYPOINT`
  running `train_player_prop_model.py` directly and `XGBOOST_DEVICE=cuda`
  baked in as a default env var. See the Dockerfile's own comment for why
  it's shaped around SageMaker's `docker run image train` contract.
- `.github/workflows/ncaafb_sagemaker_gpu_poc_build.yml` -- manual
  (`workflow_dispatch` only, never called from `ncaafb_deploy.yml`)
  build+push of that image to the shared ECR repo, tag
  `ncaafb-sagemaker-gpu-poc-latest`.
- `invoke.py`/`benchmark_entry_point.py` -- the original script-mode
  path, kept as a fallback. Still valid if you'd rather drive this from a
  local/CloudShell Python environment than the console, but needs
  `pip install sagemaker boto3` first (see the disk-space caveat above).

Nothing about `train_player_prop_model.py` or the S3 read/write paths
changes -- it reads `ncaafb/training-data/player_features.parquet` and
writes model artifacts under `ncaafb/player-prop-receiving-touchdowns/`
in the same model-artifacts bucket production uses, via the role this
directory's Terraform (`Terraform/iam-sagemaker-gpu-poc.tf`) creates.

## Running it

1. **Build and push the image** -- GitHub repo -> Actions ->
   "NCAAFB SageMaker GPU POC Build" -> Run workflow (on this branch).
   Pushes `<ECR_URI>:ncaafb-sagemaker-gpu-poc-latest`.
2. **Launch the training job from the SageMaker console** -- Training ->
   Training jobs -> Create training job:
   - Algorithm source: "Your own algorithm container in ECR" -- paste
     the image URI from step 1.
   - IAM role: the role from `terraform output -raw
     sagemaker_gpu_poc_role_arn` (or find it in IAM -- name contains
     `sagemaker-gpu-poc`).
   - Instance type: `ml.g4dn.4xlarge`, 1 instance (see "Read this before
     you run it" above for why not the cheaper `ml.g4dn.xlarge`).
   - Input data channels: none -- the script reads its training data
     straight from S3 itself, not through a SageMaker channel.
   - Output data location: any throwaway S3 prefix (e.g.
     `s3://<bucket-from-terraform-output-model_artifacts_bucket>/sagemaker-poc-output/`)
     -- required by the console but unused, since the script writes model
     artifacts directly to its own S3 key.
   - Environment variables: `MODEL_ARTIFACTS_BUCKET_NAME` (bucket name,
     not ARN -- `terraform output -raw model_artifacts_bucket`),
     `TARGET_STAT=receiving_touchdowns`, `AWS_REGION=us-east-2`.
     (`XGBOOST_DEVICE=cuda` is already baked into the image -- no need to
     set it again here.)
   - Stopping condition: max runtime an hour or so is plenty of headroom.
   - Create training job. The console's job detail page streams
     CloudWatch logs and shows billable training time when it finishes.

**This has not been run against real AWS** -- built and reasoned through,
but not dry-run-verified end to end (no AWS access in the environment
this was written in). Expect to possibly debug a container/SageMaker
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

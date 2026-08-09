"""
Manual, one-off launcher for the SageMaker GPU training benchmark. NOT
part of any automated pipeline, not invoked by Terraform/Step Functions/
CI -- you run this yourself, once, to get a real wall-clock number for
whether GPU acceleration is worth pursuing further. See this directory's
README.md for the full picture, including why RandomForest -- one of the
four candidates train_player_prop_model.py's tournament competes against
each other -- won't benefit from any of this at all.

Usage:
    pip install sagemaker boto3
    python invoke.py --role-arn $(terraform output -raw sagemaker_gpu_poc_role_arn) \\
        --bucket $(terraform output -raw model_artifacts_bucket 2>/dev/null || echo <bucket-name>)

Needs local AWS credentials able to call sagemaker:CreateTrainingJob and
iam:PassRole on the role this launches under.
"""
import argparse
import os

from sagemaker.xgboost.estimator import XGBoost

_HERE = os.path.dirname(os.path.abspath(__file__))
_NCAAFB_TRAINING_DIR = os.path.dirname(_HERE)
_SOURCE_DIR = os.path.dirname(os.path.dirname(_NCAAFB_TRAINING_DIR))
_LIBRARY_DIR = os.path.join(_SOURCE_DIR, "library")
_TRAINING_SCRIPT = os.path.join(_NCAAFB_TRAINING_DIR, "train_player_prop_model.py")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--role-arn", default=os.environ.get("SAGEMAKER_GPU_POC_ROLE_ARN"),
        help="Execution role ARN (default: $SAGEMAKER_GPU_POC_ROLE_ARN; or "
             "`terraform output -raw sagemaker_gpu_poc_role_arn`)",
    )
    parser.add_argument(
        "--bucket", default=os.environ.get("MODEL_ARTIFACTS_BUCKET_NAME"),
        help="Model artifacts bucket name (default: $MODEL_ARTIFACTS_BUCKET_NAME)",
    )
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-2"))
    parser.add_argument(
        "--target-stat", default="receiving_touchdowns",
        help="TARGET_STAT to benchmark -- receiving_touchdowns is today's slowest NCAAFB "
             "player-prop target (~43 min on Fargate)",
    )
    parser.add_argument(
        "--instance-type", default="ml.g4dn.4xlarge",
        help="16 vCPU + 1 T4 GPU -- deliberately matched to today's Fargate training_task_vcpu "
             "(16), not the cheaper ml.g4dn.xlarge (4 vCPU). RandomForestRegressorAdapter/ "
             "ElasticNetAdapter/MLPRegressorAdapter run on CPU regardless of instance type; "
             "fewer vCPUs than production would slow their share of the run and confound "
             "whatever this benchmark says about GPU's actual effect.",
    )
    args = parser.parse_args()

    if not args.role_arn:
        raise SystemExit(
            "--role-arn required (or set SAGEMAKER_GPU_POC_ROLE_ARN) -- "
            "see `terraform output -raw sagemaker_gpu_poc_role_arn`"
        )
    if not args.bucket:
        raise SystemExit("--bucket required (or set MODEL_ARTIFACTS_BUCKET_NAME)")

    estimator = XGBoost(
        entry_point="benchmark_entry_point.py",
        source_dir=_HERE,
        dependencies=[_LIBRARY_DIR, _TRAINING_SCRIPT],
        framework_version="1.7-1",
        instance_type=args.instance_type,
        instance_count=1,
        role=args.role_arn,
        base_job_name="ncaafb-player-prop-gpu-poc",
        environment={
            "MODEL_ARTIFACTS_BUCKET_NAME": args.bucket,
            "TARGET_STAT": args.target_stat,
            "AWS_REGION": args.region,
            # Read by library/ml/model_types.py's _XGB_GPU_DEVICE -- unset
            # anywhere else in this project, so this run is the only place
            # XGBoost actually trains on the GPU instead of CPU.
            "XGBOOST_DEVICE": "cuda",
        },
    )

    print(f"Starting SageMaker training job on {args.instance_type} for TARGET_STAT={args.target_stat} ...")
    estimator.fit(wait=True, logs="All")

    duration_seconds = estimator.latest_training_job.describe()["TrainingTimeInSeconds"]
    print(f"Done. Billable training time: {duration_seconds}s ({duration_seconds / 60:.1f} min)")


if __name__ == "__main__":
    main()

"""
Training/serving skew report for one sport: compares the model inputs behind
each pre-event snapshot with the training rows rebuilt for those events.
Read-only against the model-artifacts bucket.

    python feature-engineering/skew_report.py nfl --bucket <model-artifacts bucket> [--out report.json]
"""
import argparse
import json
import os
import sys
from pathlib import Path

from library.aws.s3_manager import S3Manager
from library.ml import skew_report


def _inside_working_directory(parser: argparse.ArgumentParser, path: str) -> Path:
    """`path` resolved; exits with a usage error when it falls outside the working directory."""
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(Path.cwd().resolve()):
        parser.error(f"--out must be inside the working directory: {path}")
    return resolved


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Training/serving skew report for one sport.")
    parser.add_argument("sport")
    parser.add_argument("--bucket", default=os.environ.get("MODEL_ARTIFACTS_BUCKET_NAME"), required="MODEL_ARTIFACTS_BUCKET_NAME" not in os.environ)
    parser.add_argument("--out", help="write the full report as JSON to this path")
    args = parser.parse_args(argv)

    s3 = S3Manager(args.bucket)
    report = skew_report.build_report(
        args.sport, skew_report.load_captured(s3, args.sport), skew_report.load_training_rows(s3, args.sport),
    )
    if args.out:
        with open(_inside_working_directory(parser, args.out), "w", encoding="utf-8") as out:
            json.dump(report, out, indent=2, default=str)
    sys.stdout.write(skew_report.summary(report) + "\n")


if __name__ == "__main__":
    main()

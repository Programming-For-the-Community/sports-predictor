"""Tags the 2 CloudWatch alarms (Alarm{High,Low}) that AWS Application Auto
Scaling auto-creates for an ECS capacity provider's managed_scaling
policy. Run from .github/workflows/tf_install.yml after `terraform apply`.

Usage: python3 tag_capacity_provider_alarms.py <capacity-provider-name> <region> <tag=value> [<tag=value> ...]
"""
import json
import subprocess
import sys


def main() -> None:
    capacity_provider_name, region, *tag_args = sys.argv[1:]
    tags = dict(arg.split("=", 1) for arg in tag_args)

    prefix = f"TargetTracking-{capacity_provider_name}-"
    describe = subprocess.run(
        ["aws", "cloudwatch", "describe-alarms", "--alarm-name-prefix", prefix, "--region", region, "--output", "json"],
        check=True, capture_output=True, text=True,
    )
    arns = [alarm["AlarmArn"] for alarm in json.loads(describe.stdout)["MetricAlarms"]]

    if not arns:
        raise SystemExit(f"No CloudWatch alarms found with prefix '{prefix}' in {region}")

    tag_args_cli = [f"Key={k},Value={v}" for k, v in tags.items()]
    for arn in arns:
        subprocess.run(
            ["aws", "cloudwatch", "tag-resource", "--resource-arn", arn, "--region", region, "--tags", *tag_args_cli],
            check=True,
        )
        print(f"Tagged {arn}")


if __name__ == "__main__":
    main()
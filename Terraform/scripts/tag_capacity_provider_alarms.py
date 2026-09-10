"""Tags the 2 CloudWatch alarms (Alarm{High,Low}) that AWS Application Auto
Scaling auto-creates for an ECS capacity provider's own managed_scaling
target-tracking policy -- see ec2-training-asg.tf's own comment on
aws_ecs_capacity_provider.ec2_training_spot for why these have no
Terraform resource of their own to attach a tags block to. Run as its own
step in .github/workflows/tf_install.yml right after `terraform apply`,
once per capacity provider -- not a Terraform local-exec provisioner, so a
script failure here can't taint/fail the capacity provider resource
itself, and it stays visible as a plain, independently-retriable CI step.

Shells out to the `aws` CLI rather than boto3 -- the same tool `terraform
apply` itself already needs credentials for, so nothing extra to install
on whatever machine runs it (CI runner or a developer's own machine).

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
        # managed_scaling's own alarms are created synchronously with the
        # capacity provider, so an empty result here normally means the
        # prefix itself is wrong, not a timing issue -- fail loudly rather
        # than silently leaving the alarms untagged.
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
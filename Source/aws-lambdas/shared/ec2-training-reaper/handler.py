"""
EC2 training-reaper Lambda: independent backstop against orphaned EC2
training instances -- terminates any instance tagged Component=training
that's been running past a short grace period with zero ECS tasks
assigned to it.

Complements Terraform/sfn-training-orchestrator.tf's own explicit
ScaleDownTrainingSpotCapacity/ScaleDownTrainingOnDemandCapacity states,
which only run on normal completion -- this covers the case those states
structurally can't: a manually-stopped execution runs no further states
at all, so nothing else notices the demand is gone. A real run also left
instances running and billing for 30-40+ minutes after a normal SUCCEEDED
completion, since ECS managed_scaling's own reactive scale-in is
deliberately conservative and not tunable away entirely -- this Lambda's
own schedule (Terraform/scheduler-ec2-training-reaper.tf) closes that gap
too, independent of how or whether the orchestrator's own cleanup ran.

Uses autoscaling:TerminateInstanceInAutoScalingGroup with
ShouldDecrementDesiredCapacity=True, not a raw ec2:TerminateInstances --
the latter would leave the ASG's desired capacity stale and it would just
launch a replacement instance in response.

Required environment variables:
    ECS_CLUSTER_NAME
    GRACE_PERIOD_MINUTES (default 5 -- a freshly-launched instance is
        still registering with ECS and shouldn't be reaped as "idle")
"""
import logging
import os
from datetime import datetime, timedelta, timezone

import boto3

logger = logging.getLogger("ec2-training-reaper")
logger.setLevel(logging.INFO)

# Explicit region_name -- Lambda always sets AWS_REGION in the real
# execution environment, but these clients construct at import time, and
# CI's test-collection environment has no AWS_REGION at all, so an
# unqualified boto3.client() raises NoRegionError before a single test
# can even run (real CI failure, 2026-09-02). Same fix
# lambda-cloudwatch-geo-widget's own handler.py already uses.
_REGION = os.environ.get("AWS_REGION", "us-east-2")
ec2 = boto3.client("ec2", region_name=_REGION)
ecs = boto3.client("ecs", region_name=_REGION)
autoscaling = boto3.client("autoscaling", region_name=_REGION)


def _describe_container_instances_in_batches(cluster_name: str, container_instance_arns: list[str]) -> list[dict]:
    # ECS caps DescribeContainerInstances at 100 ARNs per call.
    results = []
    for i in range(0, len(container_instance_arns), 100):
        batch = container_instance_arns[i:i + 100]
        results.extend(ecs.describe_container_instances(cluster=cluster_name, containerInstances=batch)["containerInstances"])
    return results


def idle_training_instances(cluster_name: str, grace_period_minutes: int) -> list[str]:
    """EC2 instance IDs tagged Component=training, running past the grace
    period, with zero ECS tasks (running or pending) currently assigned --
    including instances that never registered as an ECS container instance
    at all, which past the grace period is itself a sign something's
    stuck, not a reason to leave it running."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=grace_period_minutes)
    candidates = []
    paginator = ec2.get_paginator("describe_instances")
    for page in paginator.paginate(Filters=[
        {"Name": "tag:Component", "Values": ["training"]},
        {"Name": "instance-state-name", "Values": ["running"]},
    ]):
        for reservation in page["Reservations"]:
            for instance in reservation["Instances"]:
                if instance["LaunchTime"] <= cutoff:
                    candidates.append(instance["InstanceId"])
    if not candidates:
        return []

    container_instance_arns = ecs.list_container_instances(cluster=cluster_name)["containerInstanceArns"]
    idle_ids = set(candidates)
    if container_instance_arns:
        for ci in _describe_container_instances_in_batches(cluster_name, container_instance_arns):
            if ci["ec2InstanceId"] in idle_ids and (ci["runningTasksCount"] > 0 or ci["pendingTasksCount"] > 0):
                idle_ids.discard(ci["ec2InstanceId"])
    return list(idle_ids)


def lambda_handler(event, context):
    cluster_name = os.environ["ECS_CLUSTER_NAME"]
    grace_period_minutes = int(os.environ.get("GRACE_PERIOD_MINUTES", "5"))

    reaped = idle_training_instances(cluster_name, grace_period_minutes)
    for instance_id in reaped:
        logger.info("Reaping idle training instance %s", instance_id)
        autoscaling.terminate_instance_in_auto_scaling_group(
            InstanceId=instance_id, ShouldDecrementDesiredCapacity=True,
        )

    return {"reaped_instance_ids": reaped}

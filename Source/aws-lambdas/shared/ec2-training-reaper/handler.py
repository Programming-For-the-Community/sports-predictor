"""
EC2 training-reaper Lambda: independent backstop against orphaned EC2
training instances -- terminates any instance tagged Component=training
that's been running past a short grace period with zero ECS tasks
assigned to it.

Complements Terraform/sfn-training-orchestrator.tf's own explicit
ScaleDownTrainingSpotCapacity/ScaleDownTrainingOnDemandCapacity states,
which only run on normal completion. No recurring schedule anywhere
triggers this Lambda -- every invocation is caused by something that
actually just happened:
  - sfn-training-orchestrator.tf's own InvokeReaperAfterCompletion state,
    a fixed 20-minute-delayed check after a normal SUCCEEDED completion
    (WaitForLingeringInstances) -- a real run left instances running and
    billing for 30-40+ minutes after DesiredCapacity was already set to
    0, since ECS managed_scaling's own reactive scale-in is deliberately
    conservative.
  - eventbridge-training-orchestrator-terminal.tf's own EventBridge rule,
    for an ABORTED/FAILED/TIMED_OUT completion -- the state machine never
    reaches its own later states at all in that case, so nothing else
    would ever notice the demand is gone.
  - This Lambda's own self-created, one-time, self-deleting EventBridge
    Scheduler retry (see _maybe_schedule_retry below) -- stopping a Step
    Functions EXECUTION doesn't stop the ECS TASK it was waiting on, so a
    training task can still be genuinely mid-run at the moment an
    ABORTED/FAILED/TIMED_OUT event fires. Rather than guessing how long
    to wait, or polling forever, this Lambda checks again a few minutes
    later, capped at MAX_RETRIES -- a schedule that only exists for the
    handful of checks immediately around one real event, then deletes
    itself either way (ActionAfterCompletion=DELETE).

Uses autoscaling:TerminateInstanceInAutoScalingGroup with
ShouldDecrementDesiredCapacity=True, not a raw ec2:TerminateInstances --
the latter would leave the ASG's desired capacity stale and it would just
launch a replacement instance in response.

Required environment variables:
    ECS_CLUSTER_NAME
    PROJECT_TAG_VALUE -- scopes the describe_instances candidate search to
        this project's own instances (Project tag), not just Component=
        training -- that value alone isn't guaranteed unique across the
        account, and an instance outside this project's own two ASGs
        would never show up as busy in ECS_CLUSTER_NAME's own container-
        instance list, so it would never get excluded by the idle check
        either.
    SCHEDULER_GROUP_NAME, SCHEDULER_ROLE_ARN -- where/how a retry
        schedule is created (see _maybe_schedule_retry).

Optional environment variables:
    GRACE_PERIOD_MINUTES (default 5 -- a freshly-launched instance is
        still registering with ECS and shouldn't be reaped as "idle")
    RETRY_DELAY_MINUTES (default 15), MAX_RETRIES (default 3)

Event shape: {"retry_count": int} -- absent/0 for the first invocation of
a given completion/abort; incremented by each self-created retry.
"""
import json
import logging
import os
import uuid
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
scheduler = boto3.client("scheduler", region_name=_REGION)


def _describe_container_instances_in_batches(cluster_name: str, container_instance_arns: list[str]) -> list[dict]:
    # ECS caps DescribeContainerInstances at 100 ARNs per call.
    results = []
    for i in range(0, len(container_instance_arns), 100):
        batch = container_instance_arns[i:i + 100]
        results.extend(ecs.describe_container_instances(cluster=cluster_name, containerInstances=batch)["containerInstances"])
    return results


def _running_training_instances(project_tag_value: str) -> list[dict]:
    """Every EC2 instance tagged Project=<project_tag_value>/Component=
    training currently in the `running` state, regardless of grace period
    or idle status -- used both by idle_training_instances (below) and by
    _maybe_schedule_retry to decide whether there's anything left worth
    checking on later."""
    instances = []
    paginator = ec2.get_paginator("describe_instances")
    for page in paginator.paginate(Filters=[
        {"Name": "tag:Project", "Values": [project_tag_value]},
        {"Name": "tag:Component", "Values": ["training"]},
        {"Name": "instance-state-name", "Values": ["running"]},
    ]):
        for reservation in page["Reservations"]:
            instances.extend(reservation["Instances"])
    return instances


def idle_training_instances(cluster_name: str, project_tag_value: str, grace_period_minutes: int) -> list[str]:
    """EC2 instance IDs tagged Project=<project_tag_value> and
    Component=training, running past the grace period, with zero ECS
    tasks (running or pending) currently assigned -- including instances
    that never registered as an ECS container instance at all, which past
    the grace period is itself a sign something's stuck, not a reason to
    leave it running."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=grace_period_minutes)
    candidates = [i["InstanceId"] for i in _running_training_instances(project_tag_value) if i["LaunchTime"] <= cutoff]
    if not candidates:
        return []

    container_instance_arns = ecs.list_container_instances(cluster=cluster_name)["containerInstanceArns"]
    idle_ids = set(candidates)
    if container_instance_arns:
        for ci in _describe_container_instances_in_batches(cluster_name, container_instance_arns):
            if ci["ec2InstanceId"] in idle_ids and (ci["runningTasksCount"] > 0 or ci["pendingTasksCount"] > 0):
                idle_ids.discard(ci["ec2InstanceId"])
    return list(idle_ids)


def _maybe_schedule_retry(context, project_tag_value: str, retry_count: int, retry_delay_minutes: int, max_retries: int) -> None:
    """Self-creates one one-time, self-deleting EventBridge Scheduler
    schedule (ActionAfterCompletion=DELETE -- no manual cleanup needed
    either way) that re-invokes this same Lambda in retry_delay_minutes,
    only when there's still a real training-tagged instance running AND
    the retry budget isn't exhausted. Does nothing once every training
    instance is gone, or past max_retries -- this Lambda never
    perpetuates itself indefinitely."""
    if retry_count >= max_retries:
        logger.info("Reached MAX_RETRIES (%d) -- not scheduling another check.", max_retries)
        return
    if not _running_training_instances(project_tag_value):
        logger.info("No training instances still running -- nothing left to check on later.")
        return

    fire_at = datetime.now(timezone.utc) + timedelta(minutes=retry_delay_minutes)
    name = f"ec2-training-reaper-retry-{uuid.uuid4().hex[:8]}"
    scheduler.create_schedule(
        Name=name,
        GroupName=os.environ["SCHEDULER_GROUP_NAME"],
        ScheduleExpression=f"at({fire_at.strftime('%Y-%m-%dT%H:%M:%S')})",
        ScheduleExpressionTimezone="UTC",
        FlexibleTimeWindow={"Mode": "OFF"},
        ActionAfterCompletion="DELETE",
        Target={
            "Arn": context.invoked_function_arn,
            "RoleArn": os.environ["SCHEDULER_ROLE_ARN"],
            "Input": json.dumps({"retry_count": retry_count + 1}),
        },
    )
    logger.info("Scheduled retry #%d (%s) for %s", retry_count + 1, name, fire_at.isoformat())


def lambda_handler(event, context):
    cluster_name = os.environ["ECS_CLUSTER_NAME"]
    project_tag_value = os.environ["PROJECT_TAG_VALUE"]
    grace_period_minutes = int(os.environ.get("GRACE_PERIOD_MINUTES", "5"))
    retry_delay_minutes = int(os.environ.get("RETRY_DELAY_MINUTES", "15"))
    max_retries = int(os.environ.get("MAX_RETRIES", "3"))
    retry_count = int(event.get("retry_count", 0) if event else 0)

    reaped = idle_training_instances(cluster_name, project_tag_value, grace_period_minutes)
    for instance_id in reaped:
        logger.info("Reaping idle training instance %s", instance_id)
        autoscaling.terminate_instance_in_auto_scaling_group(
            InstanceId=instance_id, ShouldDecrementDesiredCapacity=True,
        )

    _maybe_schedule_retry(context, project_tag_value, retry_count, retry_delay_minutes, max_retries)

    return {"reaped_instance_ids": reaped}

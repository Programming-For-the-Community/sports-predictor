"""
Unit tests for the ec2-training-reaper Lambda's handler -- an independent
backstop that reaps idle EC2 training instances a manually-stopped
execution (or a slow native ECS scale-in) would otherwise leave orphaned.
All four boto3 clients (ec2/ecs/autoscaling/scheduler) are mocked; no AWS
involved.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

shared_ec2_training_reaper = sys.modules["shared_ec2_training_reaper"]

NOW = datetime.now(timezone.utc)
OLD_ENOUGH = NOW - timedelta(minutes=10)
TOO_RECENT = NOW - timedelta(minutes=1)


def _instances_page(*instances):
    return {"Reservations": [{"Instances": list(instances)}]} if instances else {"Reservations": []}


def _instance(instance_id, launch_time):
    return {"InstanceId": instance_id, "LaunchTime": launch_time}


class TestIdleTrainingInstances:
    def test_no_candidates_returns_empty_without_calling_ecs(self):
        with patch.object(shared_ec2_training_reaper, "ec2") as mock_ec2, \
             patch.object(shared_ec2_training_reaper, "ecs") as mock_ecs:
            mock_ec2.get_paginator.return_value.paginate.return_value = [_instances_page()]

            result = shared_ec2_training_reaper.idle_training_instances("cluster", "sports-predictor", 5)

        assert result == []
        mock_ecs.list_container_instances.assert_not_called()

    def test_instance_still_within_grace_period_is_not_a_candidate(self):
        with patch.object(shared_ec2_training_reaper, "ec2") as mock_ec2, \
             patch.object(shared_ec2_training_reaper, "ecs") as mock_ecs:
            mock_ec2.get_paginator.return_value.paginate.return_value = [
                _instances_page(_instance("i-fresh", TOO_RECENT)),
            ]

            result = shared_ec2_training_reaper.idle_training_instances("cluster", "sports-predictor", 5)

        assert result == []
        mock_ecs.list_container_instances.assert_not_called()

    def test_instance_past_grace_period_with_no_running_or_pending_tasks_is_reaped(self):
        with patch.object(shared_ec2_training_reaper, "ec2") as mock_ec2, \
             patch.object(shared_ec2_training_reaper, "ecs") as mock_ecs:
            mock_ec2.get_paginator.return_value.paginate.return_value = [
                _instances_page(_instance("i-idle", OLD_ENOUGH)),
            ]
            mock_ecs.list_container_instances.return_value = {"containerInstanceArns": ["arn:ci-1"]}
            mock_ecs.describe_container_instances.return_value = {
                "containerInstances": [
                    {"ec2InstanceId": "i-idle", "runningTasksCount": 0, "pendingTasksCount": 0},
                ]
            }

            result = shared_ec2_training_reaper.idle_training_instances("cluster", "sports-predictor", 5)

        assert result == ["i-idle"]

    def test_instance_with_a_running_task_is_not_reaped(self):
        with patch.object(shared_ec2_training_reaper, "ec2") as mock_ec2, \
             patch.object(shared_ec2_training_reaper, "ecs") as mock_ecs:
            mock_ec2.get_paginator.return_value.paginate.return_value = [
                _instances_page(_instance("i-busy", OLD_ENOUGH)),
            ]
            mock_ecs.list_container_instances.return_value = {"containerInstanceArns": ["arn:ci-1"]}
            mock_ecs.describe_container_instances.return_value = {
                "containerInstances": [
                    {"ec2InstanceId": "i-busy", "runningTasksCount": 1, "pendingTasksCount": 0},
                ]
            }

            result = shared_ec2_training_reaper.idle_training_instances("cluster", "sports-predictor", 5)

        assert result == []

    def test_instance_with_a_pending_task_is_not_reaped(self):
        with patch.object(shared_ec2_training_reaper, "ec2") as mock_ec2, \
             patch.object(shared_ec2_training_reaper, "ecs") as mock_ecs:
            mock_ec2.get_paginator.return_value.paginate.return_value = [
                _instances_page(_instance("i-pending", OLD_ENOUGH)),
            ]
            mock_ecs.list_container_instances.return_value = {"containerInstanceArns": ["arn:ci-1"]}
            mock_ecs.describe_container_instances.return_value = {
                "containerInstances": [
                    {"ec2InstanceId": "i-pending", "runningTasksCount": 0, "pendingTasksCount": 1},
                ]
            }

            result = shared_ec2_training_reaper.idle_training_instances("cluster", "sports-predictor", 5)

        assert result == []

    def test_instance_that_never_registered_as_a_container_instance_is_reaped(self):
        """Past the grace period with no ECS registration at all is itself
        a sign something's stuck, not a reason to leave it running."""
        with patch.object(shared_ec2_training_reaper, "ec2") as mock_ec2, \
             patch.object(shared_ec2_training_reaper, "ecs") as mock_ecs:
            mock_ec2.get_paginator.return_value.paginate.return_value = [
                _instances_page(_instance("i-never-registered", OLD_ENOUGH)),
            ]
            mock_ecs.list_container_instances.return_value = {"containerInstanceArns": []}

            result = shared_ec2_training_reaper.idle_training_instances("cluster", "sports-predictor", 5)

        assert result == ["i-never-registered"]
        mock_ecs.describe_container_instances.assert_not_called()

    def test_scopes_the_describe_instances_call_to_this_projects_own_tag(self):
        """Component=training alone isn't guaranteed unique across the
        account -- an instance outside this project's own two ASGs would
        never show up as busy in ECS_CLUSTER_NAME's own container-instance
        list either, so without this filter it could get reaped by
        mistake."""
        with patch.object(shared_ec2_training_reaper, "ec2") as mock_ec2, \
             patch.object(shared_ec2_training_reaper, "ecs") as mock_ecs:
            mock_paginate = mock_ec2.get_paginator.return_value.paginate
            mock_paginate.return_value = [_instances_page()]

            shared_ec2_training_reaper.idle_training_instances("cluster", "sports-predictor", 5)

        mock_paginate.assert_called_once_with(Filters=[
            {"Name": "tag:Project", "Values": ["sports-predictor"]},
            {"Name": "tag:Component", "Values": ["training"]},
            {"Name": "instance-state-name", "Values": ["running"]},
        ])

    def test_batches_describe_container_instances_past_100(self):
        arns = [f"arn:ci-{i}" for i in range(150)]
        with patch.object(shared_ec2_training_reaper, "ec2") as mock_ec2, \
             patch.object(shared_ec2_training_reaper, "ecs") as mock_ecs:
            mock_ec2.get_paginator.return_value.paginate.return_value = [
                _instances_page(_instance("i-idle", OLD_ENOUGH)),
            ]
            mock_ecs.list_container_instances.return_value = {"containerInstanceArns": arns}
            mock_ecs.describe_container_instances.return_value = {"containerInstances": []}

            shared_ec2_training_reaper.idle_training_instances("cluster", "sports-predictor", 5)

        assert mock_ecs.describe_container_instances.call_count == 2


_BASE_ENV = {
    "ECS_CLUSTER_NAME": "sports-predictor-cluster", "PROJECT_TAG_VALUE": "sports-predictor",
    "SCHEDULER_GROUP_NAME": "sports-predictor-schedules", "SCHEDULER_ROLE_ARN": "arn:aws:iam::123:role/eventbridge-invoke",
}


class TestLambdaHandler:
    """_maybe_schedule_retry is patched to a no-op throughout -- it has
    its own dedicated test class below; these tests are only about the
    reap-and-terminate behavior."""

    def test_terminates_each_idle_instance_with_desired_capacity_decrement(self):
        with patch.object(shared_ec2_training_reaper, "idle_training_instances", return_value=["i-idle-1", "i-idle-2"]), \
             patch.object(shared_ec2_training_reaper, "autoscaling") as mock_autoscaling, \
             patch.object(shared_ec2_training_reaper, "_maybe_schedule_retry"), \
             patch.dict(os.environ, _BASE_ENV):

            result = shared_ec2_training_reaper.lambda_handler({}, None)

        assert result == {"reaped_instance_ids": ["i-idle-1", "i-idle-2"]}
        assert mock_autoscaling.terminate_instance_in_auto_scaling_group.call_count == 2
        mock_autoscaling.terminate_instance_in_auto_scaling_group.assert_any_call(
            InstanceId="i-idle-1", ShouldDecrementDesiredCapacity=True,
        )

    def test_no_idle_instances_terminates_nothing(self):
        with patch.object(shared_ec2_training_reaper, "idle_training_instances", return_value=[]), \
             patch.object(shared_ec2_training_reaper, "autoscaling") as mock_autoscaling, \
             patch.object(shared_ec2_training_reaper, "_maybe_schedule_retry"), \
             patch.dict(os.environ, _BASE_ENV):

            result = shared_ec2_training_reaper.lambda_handler({}, None)

        assert result == {"reaped_instance_ids": []}
        mock_autoscaling.terminate_instance_in_auto_scaling_group.assert_not_called()

    def test_default_grace_period_is_5_minutes_when_unset(self):
        with patch.object(shared_ec2_training_reaper, "idle_training_instances", return_value=[]) as mock_idle, \
             patch.object(shared_ec2_training_reaper, "autoscaling"), \
             patch.object(shared_ec2_training_reaper, "_maybe_schedule_retry"), \
             patch.dict(os.environ, _BASE_ENV, clear=True):

            shared_ec2_training_reaper.lambda_handler({}, None)

        mock_idle.assert_called_once_with("sports-predictor-cluster", "sports-predictor", 5)

    def test_retry_count_defaults_to_0_and_is_read_from_the_event(self):
        with patch.object(shared_ec2_training_reaper, "idle_training_instances", return_value=[]), \
             patch.object(shared_ec2_training_reaper, "autoscaling"), \
             patch.object(shared_ec2_training_reaper, "_maybe_schedule_retry") as mock_schedule, \
             patch.dict(os.environ, _BASE_ENV):

            shared_ec2_training_reaper.lambda_handler({}, None)
            shared_ec2_training_reaper.lambda_handler({"retry_count": 2}, None)

        first_call, second_call = mock_schedule.call_args_list
        assert first_call.args[2] == 0  # (context, project_tag_value, retry_count, ...)
        assert second_call.args[2] == 2

    def test_default_retry_delay_and_max_retries_when_unset(self):
        with patch.object(shared_ec2_training_reaper, "idle_training_instances", return_value=[]), \
             patch.object(shared_ec2_training_reaper, "autoscaling"), \
             patch.object(shared_ec2_training_reaper, "_maybe_schedule_retry") as mock_schedule, \
             patch.dict(os.environ, _BASE_ENV, clear=True):

            shared_ec2_training_reaper.lambda_handler({}, None)

        _context, _project, _retry_count, retry_delay_minutes, max_retries = mock_schedule.call_args.args
        assert retry_delay_minutes == 15
        assert max_retries == 3


class TestMaybeScheduleRetry:
    def _context(self):
        context = MagicMock()
        context.invoked_function_arn = "arn:aws:lambda:us-east-2:123:function:sports-predictor-ec2-training-reaper"
        return context

    def test_does_nothing_once_max_retries_is_reached(self):
        with patch.object(shared_ec2_training_reaper, "scheduler") as mock_scheduler, \
             patch.object(shared_ec2_training_reaper, "ec2") as mock_ec2:
            shared_ec2_training_reaper._maybe_schedule_retry(self._context(), "sports-predictor", 3, 15, 3)

        mock_scheduler.create_schedule.assert_not_called()
        mock_ec2.get_paginator.assert_not_called()  # short-circuits before even checking

    def test_does_nothing_when_no_training_instances_are_still_running(self):
        with patch.object(shared_ec2_training_reaper, "scheduler") as mock_scheduler, \
             patch.object(shared_ec2_training_reaper, "ec2") as mock_ec2, \
             patch.dict(os.environ, _BASE_ENV):
            mock_ec2.get_paginator.return_value.paginate.return_value = [_instances_page()]

            shared_ec2_training_reaper._maybe_schedule_retry(self._context(), "sports-predictor", 0, 15, 3)

        mock_scheduler.create_schedule.assert_not_called()

    def test_schedules_a_one_time_self_deleting_retry_when_something_is_still_running(self):
        with patch.object(shared_ec2_training_reaper, "scheduler") as mock_scheduler, \
             patch.object(shared_ec2_training_reaper, "ec2") as mock_ec2, \
             patch.dict(os.environ, _BASE_ENV):
            mock_ec2.get_paginator.return_value.paginate.return_value = [
                _instances_page(_instance("i-still-busy", NOW)),
            ]

            shared_ec2_training_reaper._maybe_schedule_retry(self._context(), "sports-predictor", 1, 15, 3)

        kwargs = mock_scheduler.create_schedule.call_args.kwargs
        assert kwargs["GroupName"] == "sports-predictor-schedules"
        assert kwargs["ActionAfterCompletion"] == "DELETE"
        assert kwargs["ScheduleExpressionTimezone"] == "UTC"
        assert kwargs["ScheduleExpression"].startswith("at(")
        assert kwargs["FlexibleTimeWindow"] == {"Mode": "OFF"}
        assert kwargs["Target"]["Arn"] == "arn:aws:lambda:us-east-2:123:function:sports-predictor-ec2-training-reaper"
        assert kwargs["Target"]["RoleArn"] == "arn:aws:iam::123:role/eventbridge-invoke"
        assert json.loads(kwargs["Target"]["Input"]) == {"retry_count": 2}  # increments the given retry_count

    def test_fires_approximately_retry_delay_minutes_from_now(self):
        with patch.object(shared_ec2_training_reaper, "scheduler") as mock_scheduler, \
             patch.object(shared_ec2_training_reaper, "ec2") as mock_ec2, \
             patch.dict(os.environ, _BASE_ENV):
            mock_ec2.get_paginator.return_value.paginate.return_value = [
                _instances_page(_instance("i-still-busy", NOW)),
            ]

            shared_ec2_training_reaper._maybe_schedule_retry(self._context(), "sports-predictor", 0, 15, 3)

        expression = mock_scheduler.create_schedule.call_args.kwargs["ScheduleExpression"]
        fire_at = datetime.strptime(expression.removeprefix("at(").removesuffix(")"), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        delta = fire_at - datetime.now(timezone.utc)
        assert timedelta(minutes=14) < delta < timedelta(minutes=16)

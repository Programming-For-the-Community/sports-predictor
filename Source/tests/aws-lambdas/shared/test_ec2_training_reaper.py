"""
Unit tests for the ec2-training-reaper Lambda's handler -- an independent
backstop that reaps idle EC2 training instances a manually-stopped
execution (or a slow native ECS scale-in) would otherwise leave orphaned.
All three boto3 clients (ec2/ecs/autoscaling) are mocked; no AWS involved.
"""
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

            result = shared_ec2_training_reaper.idle_training_instances("cluster", 5)

        assert result == []
        mock_ecs.list_container_instances.assert_not_called()

    def test_instance_still_within_grace_period_is_not_a_candidate(self):
        with patch.object(shared_ec2_training_reaper, "ec2") as mock_ec2, \
             patch.object(shared_ec2_training_reaper, "ecs") as mock_ecs:
            mock_ec2.get_paginator.return_value.paginate.return_value = [
                _instances_page(_instance("i-fresh", TOO_RECENT)),
            ]

            result = shared_ec2_training_reaper.idle_training_instances("cluster", 5)

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

            result = shared_ec2_training_reaper.idle_training_instances("cluster", 5)

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

            result = shared_ec2_training_reaper.idle_training_instances("cluster", 5)

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

            result = shared_ec2_training_reaper.idle_training_instances("cluster", 5)

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

            result = shared_ec2_training_reaper.idle_training_instances("cluster", 5)

        assert result == ["i-never-registered"]
        mock_ecs.describe_container_instances.assert_not_called()

    def test_batches_describe_container_instances_past_100(self):
        arns = [f"arn:ci-{i}" for i in range(150)]
        with patch.object(shared_ec2_training_reaper, "ec2") as mock_ec2, \
             patch.object(shared_ec2_training_reaper, "ecs") as mock_ecs:
            mock_ec2.get_paginator.return_value.paginate.return_value = [
                _instances_page(_instance("i-idle", OLD_ENOUGH)),
            ]
            mock_ecs.list_container_instances.return_value = {"containerInstanceArns": arns}
            mock_ecs.describe_container_instances.return_value = {"containerInstances": []}

            shared_ec2_training_reaper.idle_training_instances("cluster", 5)

        assert mock_ecs.describe_container_instances.call_count == 2


class TestLambdaHandler:
    def test_terminates_each_idle_instance_with_desired_capacity_decrement(self):
        with patch.object(shared_ec2_training_reaper, "idle_training_instances", return_value=["i-idle-1", "i-idle-2"]), \
             patch.object(shared_ec2_training_reaper, "autoscaling") as mock_autoscaling, \
             patch.dict(os.environ, {"ECS_CLUSTER_NAME": "sports-predictor-cluster"}):

            result = shared_ec2_training_reaper.lambda_handler({}, None)

        assert result == {"reaped_instance_ids": ["i-idle-1", "i-idle-2"]}
        assert mock_autoscaling.terminate_instance_in_auto_scaling_group.call_count == 2
        mock_autoscaling.terminate_instance_in_auto_scaling_group.assert_any_call(
            InstanceId="i-idle-1", ShouldDecrementDesiredCapacity=True,
        )

    def test_no_idle_instances_terminates_nothing(self):
        with patch.object(shared_ec2_training_reaper, "idle_training_instances", return_value=[]), \
             patch.object(shared_ec2_training_reaper, "autoscaling") as mock_autoscaling, \
             patch.dict(os.environ, {"ECS_CLUSTER_NAME": "sports-predictor-cluster"}):

            result = shared_ec2_training_reaper.lambda_handler({}, None)

        assert result == {"reaped_instance_ids": []}
        mock_autoscaling.terminate_instance_in_auto_scaling_group.assert_not_called()

    def test_default_grace_period_is_5_minutes_when_unset(self):
        with patch.object(shared_ec2_training_reaper, "idle_training_instances", return_value=[]) as mock_idle, \
             patch.object(shared_ec2_training_reaper, "autoscaling"), \
             patch.dict(os.environ, {"ECS_CLUSTER_NAME": "sports-predictor-cluster"}, clear=True):

            shared_ec2_training_reaper.lambda_handler({}, None)

        mock_idle.assert_called_once_with("sports-predictor-cluster", 5)

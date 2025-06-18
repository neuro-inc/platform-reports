from datetime import datetime, timedelta

import pytest

from platform_reports.kube_client import (
    UTC,
    ContainerStatus,
    Metadata,
    Node,
    NodeStatus,
    Pod,
    PodCondition,
    PodPhase,
    PodStatus,
    Resources,
)


class TestNode:
    def test_from_payload(self) -> None:
        now = datetime.now()
        result = Node.from_payload(
            {
                "metadata": {
                    "name": "node",
                    "creationTimestamp": now.isoformat(),
                    "labels": {"key": "value"},
                },
            }
        )

        assert result == Node(
            metadata=Metadata(
                name="node", creation_timestamp=now, labels={"key": "value"}
            ),
        )


class TestNodeStatus:
    def test_from_payload(self) -> None:
        result = NodeStatus.from_payload(
            {
                "allocatable": {
                    "cpu": "3920m",
                    "memory": "13594512Ki",
                },
                "capacity": {
                    "cpu": "4",
                    "memory": "16382864Ki",
                    "nvidia.com/gpu": 1,
                    "amd.com/gpu": 2,
                },
            }
        )

        assert result == NodeStatus(
            allocatable=Resources(
                cpu=3.92,
                memory=13920780288,
            ),
            capacity=Resources(
                cpu=4,
                memory=16776052736,
                nvidia_gpu=1,
                amd_gpu=2,
            ),
        )


class TestPod:
    def test_from_payload(self) -> None:
        now = datetime.now()
        result = Pod.from_payload(
            {
                "metadata": {
                    "name": "job",
                    "creationTimestamp": now.isoformat(),
                    "labels": {"key": "value"},
                },
                "spec": {"containers": [{"name": "job"}]},
                "status": {"phase": "Running"},
            }
        )

        assert result == Pod(
            metadata=Metadata(
                name="job", creation_timestamp=now, labels={"key": "value"}
            ),
            status=PodStatus(phase=PodPhase.RUNNING),
        )


class TestPodStatus:
    def test_is_terminated(self) -> None:
        status = PodStatus(phase=PodPhase.SUCCEEDED)
        assert status.is_terminated

        status = PodStatus(phase=PodPhase.FAILED)
        assert status.is_terminated

    def test_is_scheduled__true(self) -> None:
        status = PodStatus(phase=PodPhase.RUNNING)
        assert status.is_scheduled

        status = PodStatus(phase=PodPhase.SUCCEEDED)
        assert status.is_scheduled

        status = PodStatus(phase=PodPhase.FAILED)
        assert status.is_scheduled

        status = PodStatus(
            phase=PodPhase.PENDING,
            conditions=[
                PodCondition(
                    type=PodCondition.Type.POD_SCHEDULED,
                    last_transition_time=datetime.now(UTC),
                    status=True,
                )
            ],
        )
        assert status.is_scheduled

        status = PodStatus(
            phase=PodPhase.UNKNOWN,
            conditions=[
                PodCondition(
                    type=PodCondition.Type.POD_SCHEDULED,
                    last_transition_time=datetime.now(UTC),
                    status=True,
                )
            ],
        )
        assert status.is_scheduled

    def test_is_scheduled__false(self) -> None:
        status = PodStatus(phase=PodPhase.PENDING)
        assert not status.is_scheduled

        status = PodStatus(
            phase=PodPhase.PENDING,
            conditions=[
                PodCondition(
                    type=PodCondition.Type.POD_SCHEDULED,
                    last_transition_time=datetime.now(UTC),
                    status=False,
                )
            ],
        )
        assert not status.is_scheduled

        status = PodStatus(phase=PodPhase.UNKNOWN)
        assert not status.is_scheduled

        status = PodStatus(
            phase=PodPhase.UNKNOWN,
            conditions=[
                PodCondition(
                    type=PodCondition.Type.POD_SCHEDULED,
                    last_transition_time=datetime.now(UTC),
                    status=False,
                )
            ],
        )
        assert not status.is_scheduled

    def test_finish_date__terminated__single_container(self) -> None:
        started_at = datetime.now(UTC) - timedelta(hours=1)
        finished_at = datetime.now(UTC)
        status = PodStatus(
            phase=PodPhase.SUCCEEDED,
            container_statuses=[
                ContainerStatus(
                    {
                        "terminated": {
                            "startedAt": started_at.isoformat(),
                            "finishedAt": finished_at.isoformat(),
                        }
                    }
                )
            ],
        )

        assert status.finish_date == finished_at

    def test_finish_date__terminated__multiple_containers(self) -> None:
        started_at = datetime.now(UTC) - timedelta(hours=1)
        finished_at = datetime.now(UTC) + timedelta(hours=1)
        status = PodStatus(
            phase=PodPhase.SUCCEEDED,
            container_statuses=[
                ContainerStatus(
                    {
                        "terminated": {
                            "startedAt": started_at.isoformat(),
                            "finishedAt": finished_at.isoformat(),
                        }
                    }
                ),
                ContainerStatus(
                    {
                        "terminated": {
                            "startedAt": started_at.isoformat(),
                            "finishedAt": (
                                finished_at - timedelta(hours=0.5)
                            ).isoformat(),
                        }
                    }
                ),
            ],
        )

        assert status.finish_date == finished_at

    def test_finish_date__pending(self) -> None:
        status = PodStatus(
            phase=PodPhase.PENDING,
            container_statuses=[ContainerStatus({"waiting": {}})],
        )

        with pytest.raises(ValueError, match="Pod has not finished yet"):
            status.finish_date  # noqa: B018

    def test_finish_date__running(self) -> None:
        status = PodStatus(
            phase=PodPhase.RUNNING,
            container_statuses=[
                ContainerStatus(
                    {"running": {"startedAt": datetime.now(UTC).isoformat()}}
                ),
            ],
        )

        with pytest.raises(ValueError, match="Pod has not finished yet"):
            status.finish_date  # noqa: B018

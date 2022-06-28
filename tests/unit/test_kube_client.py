from datetime import datetime

import pytest

from platform_reports.kube_client import (
    Container,
    Metadata,
    Node,
    Pod,
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
                }
            }
        )

        assert result == Node(
            metadata=Metadata(name="node", created_at=now, labels={"key": "value"})
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
            metadata=Metadata(name="job", created_at=now, labels={"key": "value"}),
            containers=[Container(name="job")],
            status=PodStatus(phase=PodPhase.RUNNING),
        )


class TestContainer:
    def test_from_payload(self) -> None:
        result = Container.from_payload(
            {"name": "job", "resources": {"requests": {"cpu": "1"}}}
        )

        assert result == Container(name="job", resource_requests=Resources(cpu_m=1000))

    def test_from_payload_without_resource_requests(self) -> None:
        result = Container.from_payload(
            {"name": "job", "resources": {"limits": {"cpu": "1"}}}
        )
        assert result == Container(name="job")

        result = Container.from_payload({"name": "job"})
        assert result == Container(name="job")


class TestResources:
    def test_from_empty_payload(self) -> None:
        assert Resources.from_payload({}) == Resources()

    def test_from_payload_with_cpu(self) -> None:
        result = Resources.from_payload({"cpu": "1"})
        assert result == Resources(cpu_m=1000)

        result = Resources.from_payload({"cpu": "100m"})
        assert result == Resources(cpu_m=100)

    def test_from_payload_with_memory(self) -> None:
        resources = Resources.from_payload({"memory": "4294967296"})
        assert resources == Resources(memory_mb=4096)

        resources = Resources.from_payload({"memory": "4194304Ki"})
        assert resources == Resources(memory_mb=4096)

        resources = Resources.from_payload({"memory": "4096Mi"})
        assert resources == Resources(memory_mb=4096)

        resources = Resources.from_payload({"memory": "4Gi"})
        assert resources == Resources(memory_mb=4096)

        resources = Resources.from_payload({"memory": "4000000K"})
        assert resources == Resources(memory_mb=3815)

        resources = Resources.from_payload({"memory": "4000M"})
        assert resources == Resources(memory_mb=3815)

        resources = Resources.from_payload({"memory": "4G"})
        assert resources == Resources(memory_mb=3815)

        with pytest.raises(ValueError, match="'4Ti' memory format is not supported"):
            Resources.from_payload({"memory": "4Ti"})

    def test_from_payload_with_gpu(self) -> None:
        assert Resources.from_payload({"nvidia.com/gpu": 1}) == Resources(gpu=1)

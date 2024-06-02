from datetime import datetime

from platform_reports.kube_client import Metadata, Node, Pod, PodPhase, PodStatus


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
            status=PodStatus(phase=PodPhase.RUNNING),
        )

from platform_reports.kube_client import Metadata, Node


class TestNode:
    def test_from_payload(self) -> None:
        result = Node.from_payload(
            {"metadata": {"name": "node", "labels": {"key": "value"}}}
        )

        assert result == Node(metadata=Metadata(name="node", labels={"key": "value"}))

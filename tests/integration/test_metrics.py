import asyncio
from collections.abc import AsyncIterator
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

import pytest
from neuro_config_client import (
    Cluster,
    ClusterStatus,
    OrchestratorConfig,
    ResourcePreset,
)

from platform_reports.cluster import ClusterHolder
from platform_reports.kube_client import KubeClient, Node
from platform_reports.metrics import PodCreditsCollector


class _TestClusterHolder(ClusterHolder):
    def __init__(self, cluster: Cluster) -> None:
        self._cluster = cluster

    @property
    def cluster(self) -> Cluster:
        return self._cluster


@pytest.fixture
def cluster() -> Cluster:
    return Cluster(
        name="default",
        status=ClusterStatus.DEPLOYED,
        created_at=datetime.now(),
        orchestrator=OrchestratorConfig(
            job_hostname_template="",
            job_internal_hostname_template="",
            job_fallback_hostname="",
            job_schedule_timeout_s=30,
            job_schedule_scale_up_timeout_s=30,
            resource_presets=[
                ResourcePreset(
                    name="test-preset",
                    cpu=1,
                    memory=1024**3,
                    credits_per_hour=Decimal(3600),
                )
            ],
        ),
    )


@pytest.fixture
def cluster_holder(cluster: Cluster) -> ClusterHolder:
    return _TestClusterHolder(cluster)


class KubePodFactory(Protocol):
    async def __call__(
        self,
        namespace: str,
        pod: dict[str, Any],
    ) -> dict[str, Any]:
        pass


class TestPodCreditsCollector:
    @pytest.fixture
    def collector(
        self, kube_client: KubeClient, kube_node: Node, cluster_holder: ClusterHolder
    ) -> PodCreditsCollector:
        return PodCreditsCollector(
            kube_client=kube_client,
            cluster_holder=cluster_holder,
            node_name=kube_node.metadata.name,
            pod_preset_label="preset",
        )

    @pytest.fixture
    async def kube_pod_factory(
        self, kube_client: KubeClient
    ) -> AsyncIterator[KubePodFactory]:
        pods = []

        async def _create(namespace: str, pod: dict[str, Any]) -> dict[str, Any]:
            pod = await kube_client.create_raw_pod(namespace, pod)
            pods.append(pod)
            return pod

        yield _create

        for pod in pods:
            metadata = pod["metadata"]
            await kube_client.delete_pod(metadata["namespace"], metadata["name"])

    async def test_get_latest_value__pod_running(
        self,
        collector: PodCreditsCollector,
        kube_client: KubeClient,
        kube_pod_factory: KubePodFactory,
    ) -> None:
        pod = await kube_pod_factory(
            "default",
            {
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {
                    "generateName": "test-",
                    "labels": {"preset": "test-preset"},
                },
                "spec": {
                    "restartPolicy": "Never",
                    "containers": [
                        {
                            "name": "ubuntu",
                            "image": "ubuntu:20.04",
                            "command": ["bash"],
                            "args": ["-c", "sleep 60"],
                        }
                    ],
                },
            },
        )
        pod_name = pod["metadata"]["name"]

        await kube_client.wait_pod_is_running("default", pod_name)

        await asyncio.sleep(5)

        value = await collector.get_latest_value()

        assert pod_name in value
        assert value[pod_name] >= Decimal(5)

    async def test_get_latest_value__pod_terminated(
        self,
        collector: PodCreditsCollector,
        kube_client: KubeClient,
        kube_pod_factory: KubePodFactory,
    ) -> None:
        pod = await kube_pod_factory(
            "default",
            {
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {
                    "generateName": "test-",
                    "labels": {"preset": "test-preset"},
                },
                "spec": {
                    "restartPolicy": "Never",
                    "containers": [
                        {
                            "name": "ubuntu",
                            "image": "ubuntu:20.04",
                            "command": ["bash"],
                            "args": ["-c", "sleep 5"],
                        }
                    ],
                },
            },
        )
        pod_name = pod["metadata"]["name"]

        await kube_client.wait_pod_is_terminated("default", pod_name)

        value = await collector.get_latest_value()

        assert pod_name in value
        assert value[pod_name] >= Decimal(5)

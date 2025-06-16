import asyncio
from datetime import datetime
from decimal import Decimal

import pytest
from neuro_config_client import (
    Cluster,
    ClusterStatus,
    OrchestratorConfig,
    ResourcePreset,
)

from platform_reports.cluster import ClusterHolder
from platform_reports.kube_client import KubeClient
from platform_reports.metrics_collector import PodCreditsCollector

from .conftest_kube import KubePodFactory


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


class TestPodCreditsCollector:
    @pytest.fixture
    def collector(
        self, kube_client: KubeClient, cluster_holder: ClusterHolder
    ) -> PodCreditsCollector:
        return PodCreditsCollector(
            kube_client=kube_client,
            cluster_holder=cluster_holder,
        )

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
                    "labels": {
                        "platform.apolo.us/org": "test-org",
                        "platform.apolo.us/project": "test-project",
                        "platform.apolo.us/preset": "test-preset",
                    },
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
        pod_namespace = pod["metadata"]["namespace"]
        pod_name = pod["metadata"]["name"]

        await kube_client.wait_pod_is_running(pod_namespace, pod_name)

        await asyncio.sleep(2)

        value = await collector.get_latest_value()

        assert pod_name in value
        assert value[pod_name] >= Decimal(2)

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
                    "labels": {
                        "platform.apolo.us/org": "test-org",
                        "platform.apolo.us/project": "test-project",
                        "platform.apolo.us/preset": "test-preset",
                    },
                },
                "spec": {
                    "restartPolicy": "Never",
                    "containers": [
                        {
                            "name": "ubuntu",
                            "image": "ubuntu:20.04",
                            "command": ["bash"],
                            "args": ["-c", "sleep 2"],
                        }
                    ],
                },
            },
        )
        pod_namespace = pod["metadata"]["namespace"]
        pod_name = pod["metadata"]["name"]

        await kube_client.wait_pod_is_terminated(pod_namespace, pod_name)

        value = await collector.get_latest_value()

        assert pod_name in value
        assert value[pod_name] >= Decimal(2)

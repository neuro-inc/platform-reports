from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any, Awaitable, Callable

import aiohttp
import pytest

from platform_reports.kube_client import (
    EventListener,
    KubeClient,
    Pod,
    PodPhase,
    PodWatcher,
    WatchEvent,
)

PodFactory = Callable[..., Awaitable[Pod]]


class TestKubeClient:
    async def test_get_node(self, kube_client: KubeClient) -> None:
        node = await kube_client.get_node("minikube")

        assert node.metadata.name == "minikube"
        assert node.metadata.labels

    async def test_get_unknown_node__raises_error(
        self, kube_client: KubeClient
    ) -> None:
        with pytest.raises(aiohttp.ClientError):
            await kube_client.get_node("unknown")

    async def test_get_pods(self, kube_client: KubeClient) -> None:
        result = await kube_client.get_pods(namespace="kube-system")

        assert result

    async def test_get_pods_with_label_selector(self, kube_client: KubeClient) -> None:
        result = await kube_client.get_pods(
            namespace="kube-system", label_selector="k8s-app=kube-proxy"
        )

        assert result.items

        for pod in result.items:
            assert pod.metadata.name.startswith(
                "kube-proxy"
            ), f"Found pod {pod.metadata.name}"

    async def test_get_pods_with_field_selector(self, kube_client: KubeClient) -> None:
        result = await kube_client.get_pods(
            namespace="kube-system",
            field_selector=(
                "spec.nodeName=minikube,status.phase!=Failed,status.phase!=Succeeded"
            ),
        )

        assert result.items

        for pod in result.items:
            assert pod.status.phase in (
                PodPhase.PENDING,
                PodPhase.RUNNING,
            ), f"Pod {pod.metadata.name} is in {pod.status.phase.value} phase"

    async def test_get_pods_in_unknown_namespace(self, kube_client: KubeClient) -> None:
        result = await kube_client.get_pods(namespace="unknown")

        assert result.items == []


class MyPodEventListener(EventListener):
    def __init__(self) -> None:
        self.pod_names: list[str] = []
        self._events: dict[str, asyncio.Event] = {}

    async def init(self, raw_pods: list[dict[str, Any]]) -> None:
        self.pod_names.extend([p["metadata"]["name"] for p in raw_pods])

    async def handle(self, event: WatchEvent) -> None:
        pod_name = event.resource["metadata"]["name"]
        self.pod_names.append(pod_name)
        waiter = self._events.get(pod_name)
        if waiter:
            del self._events[pod_name]
            waiter.set()

    async def wait_for_pod(self, name: str) -> None:
        if name in self.pod_names:
            return
        event = asyncio.Event()
        self._events[name] = event
        await event.wait()


class TestPodWatcher:
    @pytest.fixture
    def listener(self) -> MyPodEventListener:
        return MyPodEventListener()

    @pytest.fixture
    async def pod_watcher(
        self, kube_client: KubeClient, listener: MyPodEventListener
    ) -> AsyncIterator[PodWatcher]:
        watcher = PodWatcher(kube_client)
        watcher.subscribe(listener)
        async with watcher:
            yield watcher

    @pytest.mark.usefixtures("pod_watcher")
    async def test_handle(
        self, listener: MyPodEventListener, pod_factory: PodFactory
    ) -> None:
        assert len(listener.pod_names) > 0

        pod = await pod_factory(image="gcr.io/google_containers/pause:3.1")

        await asyncio.wait_for(listener.wait_for_pod(pod.metadata.name), 5)

        assert pod.metadata.name in listener.pod_names

    async def test_subscribe_after_start(
        self, pod_watcher: PodWatcher, listener: MyPodEventListener
    ) -> None:
        with pytest.raises(
            Exception, match="Subscription is not possible after watcher start"
        ):
            pod_watcher.subscribe(listener)

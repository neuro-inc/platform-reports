from __future__ import annotations

import asyncio
import tempfile
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import aiohttp
import aiohttp.web
import pytest
from yarl import URL

from platform_reports.config import KubeClientAuthType, KubeConfig
from platform_reports.kube_client import KubeClient, KubeClientError, Node, PodPhase

from .conftest import create_local_app_server


class TestKubeClientTokenUpdater:
    @pytest.fixture
    async def kube_app(self) -> aiohttp.web.Application:
        async def _get_pods(request: aiohttp.web.Request) -> aiohttp.web.Response:
            auth = request.headers["Authorization"]
            token = auth.split()[-1]
            app["token"]["value"] = token
            return aiohttp.web.json_response({"kind": "PodList", "items": []})

        app = aiohttp.web.Application()
        app["token"] = {"value": ""}
        app.router.add_routes(
            [aiohttp.web.get("/api/v1/namespaces/default/pods", _get_pods)]
        )
        return app

    @pytest.fixture
    async def kube_server(
        self, kube_app: aiohttp.web.Application, unused_tcp_port_factory: Any
    ) -> AsyncIterator[str]:
        async with create_local_app_server(
            kube_app, port=unused_tcp_port_factory()
        ) as address:
            yield f"http://{address.host}:{address.port}"

    @pytest.fixture
    def kube_token_path(self) -> Iterator[str]:
        _, path = tempfile.mkstemp()
        Path(path).write_text("token-1")
        yield path
        Path(path).unlink()

    @pytest.fixture
    async def kube_client(
        self, kube_server: str, kube_token_path: str
    ) -> AsyncIterator[KubeClient]:
        async with KubeClient(
            config=KubeConfig(
                url=URL(kube_server),
                auth_type=KubeClientAuthType.TOKEN,
                token_path=kube_token_path,
                token_update_interval_s=1,
            )
        ) as client:
            yield client

    async def test_token_periodically_updated(
        self,
        kube_app: aiohttp.web.Application,
        kube_client: KubeClient,
        kube_token_path: str,
    ) -> None:
        await kube_client.get_pods("default")
        assert kube_app["token"]["value"] == "token-1"

        Path(kube_token_path).write_text("token-2")
        await asyncio.sleep(2)

        await kube_client.get_pods("default")
        assert kube_app["token"]["value"] == "token-2"


class TestKubeClient:
    async def test_get_node(self, kube_client: KubeClient, kube_node: Node) -> None:
        node = await kube_client.get_node(kube_node.metadata.name)

        assert node.metadata.name == kube_node.metadata.name
        assert node.metadata.labels

    async def test_get_unknown_node__raises_error(
        self, kube_client: KubeClient
    ) -> None:
        with pytest.raises(KubeClientError):
            await kube_client.get_node("unknown")

    async def test_get_pods(self, kube_client: KubeClient) -> None:
        result = await kube_client.get_pods(namespace="kube-system")

        assert result

    async def test_get_pods_with_label_selector(self, kube_client: KubeClient) -> None:
        pods = await kube_client.get_pods(
            namespace="kube-system", label_selector="k8s-app=kube-proxy"
        )

        assert pods

        for pod in pods:
            assert pod.metadata.name.startswith(
                "kube-proxy"
            ), f"Found pod {pod.metadata.name}"

    async def test_get_pods_with_field_selector(
        self, kube_client: KubeClient, kube_node: Node
    ) -> None:
        pods = await kube_client.get_pods(
            namespace="kube-system",
            field_selector=(
                f"spec.nodeName={kube_node.metadata.name},"
                "status.phase!=Failed,status.phase!=Succeeded"
            ),
        )

        assert pods

        for pod in pods:
            assert pod.status.phase in (
                PodPhase.PENDING,
                PodPhase.RUNNING,
            ), f"Pod {pod.metadata.name} is in {pod.status.phase.value} phase"

    async def test_get_pods_in_unknown_namespace(self, kube_client: KubeClient) -> None:
        result = await kube_client.get_pods(namespace="unknown")

        assert result == []

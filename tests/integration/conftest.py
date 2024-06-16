from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Coroutine, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp
import pytest
from neuro_auth_client import Permission
from pytest_docker.plugin import Services
from yarl import URL

from platform_reports.api import (
    create_grafana_proxy_app,
    create_metrics_app,
    create_prometheus_proxy_app,
)
from platform_reports.config import (
    GrafanaProxyConfig,
    KubeConfig,
    MetricsConfig,
    PlatformAuthConfig,
    PlatformServiceConfig,
    PrometheusProxyConfig,
    ServerConfig,
)
from platform_reports.kube_client import Node


@pytest.fixture(scope="session")
def docker_compose_file() -> str:
    return str(Path(__file__).parent.resolve() / "docker/docker-compose.yaml")


@pytest.fixture(scope="session")
def docker_setup() -> list[str]:
    return ["up --wait --pull always --build"]


@dataclass(frozen=True)
class Address:
    host: str
    port: int


@asynccontextmanager
async def create_local_app_server(
    app: aiohttp.web.Application, port: int = 8080
) -> AsyncIterator[Address]:
    runner = aiohttp.web.AppRunner(app)
    try:
        await runner.setup()
        addres = Address("0.0.0.0", port)
        site = aiohttp.web.TCPSite(runner, addres.host, addres.port)
        await site.start()
        yield addres
    finally:
        await runner.shutdown()
        await runner.cleanup()


UserFactory = Callable[[str, Sequence[Permission]], Coroutine[Any, Any, None]]


@pytest.fixture(scope="session")
def event_loop() -> asyncio.AbstractEventLoop:
    return asyncio.get_event_loop()


@pytest.fixture
async def service_token(
    user_factory: UserFactory, token_factory: Callable[[str], str]
) -> str:
    await user_factory("cluster", [Permission(uri="user://", action="read")])
    return token_factory("cluster")


@pytest.fixture
async def cluster_admin_token(
    user_factory: UserFactory, token_factory: Callable[[str], str]
) -> str:
    await user_factory(
        "cluster-admin",
        [
            Permission(uri="role://default/manager", action="manage"),
            Permission(uri="cluster://default/access", action="read"),
        ],
    )
    return token_factory("cluster-admin")


@pytest.fixture
async def regular_user_token(
    user_factory: UserFactory, token_factory: Callable[[str], str]
) -> str:
    await user_factory(
        "user",
        [
            Permission(uri="cluster://default/access", action="read"),
            Permission(uri="job://default/user", action="manage"),
        ],
    )
    return token_factory("user")


@pytest.fixture
async def other_cluster_user_token(
    user_factory: UserFactory, token_factory: Callable[[str], str]
) -> str:
    await user_factory(
        "other-user",
        [
            Permission(uri="cluster://neuro-public/access", action="read"),
            Permission(uri="job://neuro-public/other-user", action="manage"),
        ],
    )
    return token_factory("other-user")


@pytest.fixture
def platform_auth_server(docker_ip: str, docker_services: Services) -> URL:
    port = docker_services.port_for("platform-auth", 8080)
    return URL(f"http://{docker_ip}:{port}")


@pytest.fixture
async def platform_api_server(
    unused_tcp_port_factory: Callable[[], int],
    platform_api_app: aiohttp.web.Application,
) -> AsyncIterator[URL]:
    async with create_local_app_server(
        app=platform_api_app, port=unused_tcp_port_factory()
    ) as address:
        yield URL.build(scheme="http", host=address.host, port=address.port)


@pytest.fixture
async def platform_config_server(
    unused_tcp_port_factory: Callable[[], int],
    platform_config_app: aiohttp.web.Application,
) -> AsyncIterator[URL]:
    async with create_local_app_server(
        app=platform_config_app, port=unused_tcp_port_factory()
    ) as address:
        yield URL.build(scheme="http", host=address.host, port=address.port)


@pytest.fixture
def platform_auth_config(
    platform_auth_server: URL, service_token: str
) -> PlatformAuthConfig:
    return PlatformAuthConfig(url=platform_auth_server, token=service_token)


@pytest.fixture
def platform_api_config(
    platform_api_server: URL, service_token: str
) -> PlatformServiceConfig:
    return PlatformServiceConfig(
        url=platform_api_server / "api/v1", token=service_token
    )


@pytest.fixture
def platform_config_config(
    platform_config_server: URL, service_token: str
) -> PlatformServiceConfig:
    return PlatformServiceConfig(url=platform_config_server, token=service_token)


@pytest.fixture
def metrics_config(
    unused_tcp_port_factory: Callable[[], int],
    platform_config_config: PlatformServiceConfig,
    platform_api_config: PlatformServiceConfig,
    kube_config: KubeConfig,
    kube_node: Node,
) -> MetricsConfig:
    return MetricsConfig(
        server=ServerConfig(port=unused_tcp_port_factory()),
        platform_config=platform_config_config,
        platform_api=platform_api_config,
        kube=kube_config,
        cluster_name="default",
        node_name=kube_node.metadata.name,
    )


@pytest.fixture
async def metrics_server_factory() -> (
    Callable[[MetricsConfig], AbstractAsyncContextManager[URL]]
):
    @asynccontextmanager
    async def _create(metrics_config: MetricsConfig) -> AsyncIterator[URL]:
        app = create_metrics_app(metrics_config)
        async with create_local_app_server(
            app=app, port=metrics_config.server.port
        ) as address:
            assert app["zone"] == "minikube-zone"
            assert app["instance_type"] == "minikube"
            assert app["node_pool_name"] == "minikube-node-pool"
            yield URL.build(scheme="http", host=address.host, port=address.port)

    return _create


@pytest.fixture
async def metrics_server(
    metrics_server_factory: Callable[[MetricsConfig], AbstractAsyncContextManager[URL]],
    metrics_config: MetricsConfig,
) -> AsyncIterator[URL]:
    async with metrics_server_factory(metrics_config) as server:
        yield server


@pytest.fixture
def thanos_query_url(docker_ip: str, docker_services: Services) -> URL:
    port = docker_services.port_for("thanos-query", 9091)
    return URL(f"http://{docker_ip}:{port}")


@pytest.fixture
def prometheus_proxy_config(
    unused_tcp_port_factory: Callable[[], int],
    platform_auth_config: PlatformAuthConfig,
    platform_api_config: PlatformServiceConfig,
    thanos_query_url: URL,
) -> PrometheusProxyConfig:
    return PrometheusProxyConfig(
        server=ServerConfig(port=unused_tcp_port_factory()),
        prometheus_url=thanos_query_url,
        platform_auth=platform_auth_config,
        platform_api=platform_api_config,
        cluster_name="default",
        access_token_cookie_names=["dat"],
    )


@pytest.fixture
async def prometheus_proxy_server(
    prometheus_proxy_config: PrometheusProxyConfig,
) -> AsyncIterator[URL]:
    async with create_local_app_server(
        app=create_prometheus_proxy_app(prometheus_proxy_config),
        port=prometheus_proxy_config.server.port,
    ) as address:
        yield URL.build(scheme="http", host=address.host, port=address.port)


@pytest.fixture
def grafana_url(docker_ip: str, docker_services: Services) -> URL:
    port = docker_services.port_for("grafana", 3000)
    return URL(f"http://{docker_ip}:{port}")


@pytest.fixture
def grafana_proxy_config(
    unused_tcp_port_factory: Callable[[], int],
    platform_auth_config: PlatformAuthConfig,
    platform_api_config: PlatformServiceConfig,
    grafana_url: URL,
) -> GrafanaProxyConfig:
    return GrafanaProxyConfig(
        server=ServerConfig(port=unused_tcp_port_factory()),
        grafana_url=grafana_url,
        platform_auth=platform_auth_config,
        platform_api=platform_api_config,
        cluster_name="default",
        access_token_cookie_names=["dat"],
    )


@pytest.fixture
async def grafana_proxy_server(
    grafana_proxy_config: GrafanaProxyConfig,
) -> AsyncIterator[URL]:
    async with create_local_app_server(
        app=create_grafana_proxy_app(grafana_proxy_config),
        port=grafana_proxy_config.server.port,
    ) as address:
        yield URL.build(scheme="http", host=address.host, port=address.port)


@pytest.fixture
async def client() -> AsyncIterator[aiohttp.ClientSession]:
    async with aiohttp.ClientSession() as session:
        yield session

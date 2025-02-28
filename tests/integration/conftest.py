from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import aiohttp
import pydantic
import pytest
from pytest_docker.plugin import Services
from yarl import URL

from platform_reports.api import (
    create_grafana_proxy_app,
    create_metrics_api_app,
    create_metrics_exporter_app,
    create_prometheus_proxy_app,
)
from platform_reports.config import (
    GrafanaProxyConfig,
    KubeConfig,
    MetricsApiConfig,
    MetricsExporterConfig,
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

    @property
    def http_url(self) -> URL:
        return URL.build(scheme="http", host=self.host, port=self.port)


@asynccontextmanager
async def create_local_app_server(
    app: aiohttp.web.Application, port: int = 8080
) -> AsyncIterator[Address]:
    runner = aiohttp.web.AppRunner(app)
    try:
        await runner.setup()
        addres = Address("127.0.0.1", port)
        site = aiohttp.web.TCPSite(runner, addres.host, addres.port)
        await site.start()
        yield addres
    finally:
        await runner.shutdown()
        await runner.cleanup()


@pytest.fixture(scope="session")
def event_loop() -> Iterator[asyncio.AbstractEventLoop]:
    loop = asyncio.get_event_loop()
    yield loop
    loop.close()


@pytest.fixture()
async def platform_api_server(
    unused_tcp_port_factory: Callable[[], int],
    platform_api_app: aiohttp.web.Application,
) -> AsyncIterator[URL]:
    async with create_local_app_server(
        app=platform_api_app, port=unused_tcp_port_factory()
    ) as address:
        yield URL.build(scheme="http", host=address.host, port=address.port)


@pytest.fixture()
async def platform_config_server(
    unused_tcp_port_factory: Callable[[], int],
    platform_config_app: aiohttp.web.Application,
) -> AsyncIterator[URL]:
    async with create_local_app_server(
        app=platform_config_app, port=unused_tcp_port_factory()
    ) as address:
        yield URL.build(scheme="http", host=address.host, port=address.port)


@pytest.fixture()
def platform_auth_config(
    platform_auth_server: URL, service_token: str
) -> PlatformAuthConfig:
    return PlatformAuthConfig(url=platform_auth_server, token=service_token)


@pytest.fixture()
def platform_api_config(
    platform_api_server: URL, service_token: str
) -> PlatformServiceConfig:
    return PlatformServiceConfig(
        url=platform_api_server / "api/v1", token=service_token
    )


@pytest.fixture()
def platform_config_config(
    platform_config_server: URL, service_token: str
) -> PlatformServiceConfig:
    return PlatformServiceConfig(url=platform_config_server, token=service_token)


@pytest.fixture()
def metrics_exporter_config(
    unused_tcp_port_factory: Callable[[], int],
    platform_config_config: PlatformServiceConfig,
    platform_api_config: PlatformServiceConfig,
    kube_config: KubeConfig,
    kube_node: Node,
) -> MetricsExporterConfig:
    return MetricsExporterConfig(
        server=ServerConfig(port=unused_tcp_port_factory()),
        platform_config=platform_config_config,
        platform_api=platform_api_config,
        kube=kube_config,
        cluster_name="default",
        node_name=kube_node.metadata.name,
    )


@pytest.fixture()
async def metrics_exporter_server_factory() -> Callable[
    [MetricsExporterConfig], AbstractAsyncContextManager[URL]
]:
    @asynccontextmanager
    async def _create(
        metrics_exporter_config: MetricsExporterConfig,
    ) -> AsyncIterator[URL]:
        app = create_metrics_exporter_app(metrics_exporter_config)
        async with create_local_app_server(
            app=app, port=metrics_exporter_config.server.port
        ) as address:
            yield URL.build(scheme="http", host=address.host, port=address.port)

    return _create


@pytest.fixture()
async def metrics_exporter_server(
    metrics_exporter_server_factory: Callable[
        [MetricsExporterConfig], AbstractAsyncContextManager[URL]
    ],
    metrics_exporter_config: MetricsExporterConfig,
) -> AsyncIterator[URL]:
    async with metrics_exporter_server_factory(metrics_exporter_config) as server:
        yield server


@pytest.fixture()
def thanos_query_url(docker_ip: str, docker_services: Services) -> URL:
    port = docker_services.port_for("thanos-query", 9091)
    return URL(f"http://{docker_ip}:{port}")


@pytest.fixture()
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


@pytest.fixture()
async def prometheus_proxy_server(
    prometheus_proxy_config: PrometheusProxyConfig,
) -> AsyncIterator[URL]:
    async with create_local_app_server(
        app=create_prometheus_proxy_app(prometheus_proxy_config),
        port=prometheus_proxy_config.server.port,
    ) as address:
        yield URL.build(scheme="http", host=address.host, port=address.port)


@pytest.fixture()
def grafana_url(docker_ip: str, docker_services: Services) -> URL:
    port = docker_services.port_for("grafana", 3000)
    return URL(f"http://{docker_ip}:{port}")


@pytest.fixture()
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


@pytest.fixture()
async def grafana_proxy_server(
    grafana_proxy_config: GrafanaProxyConfig,
) -> AsyncIterator[URL]:
    async with create_local_app_server(
        app=create_grafana_proxy_app(grafana_proxy_config),
        port=grafana_proxy_config.server.port,
    ) as address:
        yield URL.build(scheme="http", host=address.host, port=address.port)


@pytest.fixture()
async def client() -> AsyncIterator[aiohttp.ClientSession]:
    async with aiohttp.ClientSession() as session:
        yield session


@pytest.fixture()
def metrics_api_config(
    unused_tcp_port_factory: Callable[[], int],
    platform_auth_config: PlatformAuthConfig,
    platform_config_config: PlatformServiceConfig,
    thanos_query_url: URL,
) -> MetricsApiConfig:
    return MetricsApiConfig(
        server=MetricsApiConfig.Server(port=unused_tcp_port_factory()),
        prometheus_url=pydantic.HttpUrl(str(thanos_query_url)),
        platform=MetricsApiConfig.PlatformConfig(
            auth_url=pydantic.HttpUrl(str(platform_auth_config.url)),
            config_url=pydantic.HttpUrl(str(platform_config_config.url)),
            token=platform_auth_config.token,
        ),
        cluster_name="default",
    )


@pytest.fixture()
async def metrics_api_server(
    metrics_api_config: MetricsApiConfig,
) -> AsyncIterator[URL]:
    app = create_metrics_api_app(metrics_api_config)
    async with create_local_app_server(
        app=app, port=metrics_api_config.server.port
    ) as address:
        yield address.http_url


@pytest.fixture()
async def exit_stack() -> AsyncIterator[AsyncExitStack]:
    async with AsyncExitStack() as stack:
        yield stack

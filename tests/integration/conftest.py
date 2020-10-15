from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Coroutine, Sequence

import aiohttp
import pytest
from neuro_auth_client import Cluster, Permission
from yarl import URL

from platform_reports.api import (
    create_grafana_proxy_app,
    create_metrics_app,
    create_prometheus_proxy_app,
)
from platform_reports.config import (
    GrafanaProxyConfig,
    MetricsConfig,
    PlatformApiConfig,
    PlatformAuthConfig,
    PrometheusProxyConfig,
    ServerConfig,
)


pytest_plugins = [
    "tests.integration.platform_auth",
    "tests.integration.platform_api",
    "tests.integration.kube",
]


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


UserFactory = Callable[
    [str, Sequence[Cluster], Sequence[Permission]], Coroutine[Any, Any, None]
]


@pytest.fixture
async def service_token(
    user_factory: UserFactory, token_factory: Callable[[str], str]
) -> str:
    await user_factory(
        "cluster", [], [Permission(uri="user://", action="read")],
    )
    return token_factory("cluster")


@pytest.fixture
async def cluster_admin_token(
    user_factory: UserFactory, token_factory: Callable[[str], str]
) -> str:
    await user_factory(
        "cluster-admin",
        [Cluster(name="default")],
        [
            Permission(uri="cluster://default/admin", action="manage"),
            Permission(uri="job://default", action="manage"),
        ],
    )
    return token_factory("cluster-admin")


@pytest.fixture
async def regular_user_token(
    user_factory: UserFactory, token_factory: Callable[[str], str]
) -> str:
    await user_factory(
        "user",
        [Cluster(name="default")],
        [Permission(uri="job://default/user", action="manage")],
    )
    return token_factory("user")


@pytest.fixture
async def other_cluster_user_token(
    user_factory: UserFactory, token_factory: Callable[[str], str]
) -> str:
    await user_factory(
        "other-user",
        [Cluster(name="neuro-public")],
        [Permission(uri="job://neuro-public/other-user", action="manage")],
    )
    return token_factory("other-user")


@pytest.fixture
def platform_auth_server() -> URL:
    return URL("http://localhost:8080")


@pytest.fixture
async def platform_api_server(
    platform_api_app: aiohttp.web.Application,
) -> AsyncIterator[URL]:
    async with create_local_app_server(app=platform_api_app, port=8380) as address:
        yield URL.build(scheme="http", host=address.host, port=address.port)


@pytest.fixture
def platform_auth_config(
    platform_auth_server: URL, service_token: str
) -> PlatformAuthConfig:
    return PlatformAuthConfig(url=platform_auth_server, token=service_token)


@pytest.fixture
def platform_api_config(
    platform_api_server: URL, service_token: str
) -> PlatformApiConfig:
    return PlatformApiConfig(url=platform_api_server / "api/v1", token=service_token)


@pytest.fixture
def metrics_config() -> MetricsConfig:
    return MetricsConfig(server=ServerConfig(port=9500), host_name="host")


@pytest.fixture
async def metrics_server(metrics_config: MetricsConfig) -> AsyncIterator[URL]:
    async with create_local_app_server(
        app=create_metrics_app(metrics_config), port=metrics_config.server.port,
    ) as address:
        yield URL.build(scheme="http", host=address.host, port=address.port)


@pytest.fixture
def prometheus_proxy_config(
    platform_auth_config: PlatformAuthConfig, platform_api_config: PlatformApiConfig
) -> PrometheusProxyConfig:
    return PrometheusProxyConfig(
        server=ServerConfig(port=8180),
        prometheus_server=ServerConfig(port=9090),
        platform_auth=platform_auth_config,
        platform_api=platform_api_config,
        cluster_name="default",
        access_token_cookie_name="dat",
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
def grafana_proxy_config(
    platform_auth_config: PlatformAuthConfig, platform_api_config: PlatformApiConfig
) -> GrafanaProxyConfig:
    return GrafanaProxyConfig(
        server=ServerConfig(port=8280),
        public_server=ServerConfig(port=8280),
        grafana_server=ServerConfig(port=3000),
        platform_auth=platform_auth_config,
        platform_api=platform_api_config,
        cluster_name="default",
        access_token_cookie_name="dat",
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
async def client(
    grafana_proxy_config: GrafanaProxyConfig,
) -> AsyncIterator[aiohttp.ClientSession]:
    async with aiohttp.ClientSession() as session:
        yield session

import logging
import os
from dataclasses import dataclass, field
from typing import Dict, Optional

from aiohttp.client import DEFAULT_TIMEOUT, ClientTimeout
from yarl import URL


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ServerConfig:
    scheme: str = "http"
    host: str = "0.0.0.0"
    port: int = 8080


@dataclass(frozen=True)
class PlatformAuthConfig:
    url: URL
    token: str = field(repr=False)


@dataclass(frozen=True)
class PlatformApiConfig:
    url: URL
    token: str = field(repr=False)


@dataclass(frozen=True)
class PrometheusProxyConfig:
    server: ServerConfig
    prometheus_server: ServerConfig
    platform_auth: PlatformAuthConfig
    platform_api: PlatformApiConfig
    cluster_name: str
    access_token_cookie_name: str
    timeout: ClientTimeout = DEFAULT_TIMEOUT


@dataclass(frozen=True)
class GrafanaProxyConfig:
    server: ServerConfig
    public_server: ServerConfig
    grafana_server: ServerConfig
    platform_auth: PlatformAuthConfig
    platform_api: PlatformApiConfig
    cluster_name: str
    access_token_cookie_name: str
    timeout: ClientTimeout = DEFAULT_TIMEOUT


class EnvironConfigFactory:
    def __init__(self, environ: Optional[Dict[str, str]] = None) -> None:
        self._environ = environ or os.environ

    def create_prometheus_proxy(self) -> PrometheusProxyConfig:
        return PrometheusProxyConfig(
            server=self._create_server(),
            prometheus_server=self._create_prometheus_server(),
            platform_auth=self._create_platform_auth(),
            platform_api=self._create_platform_api(),
            cluster_name=self._environ["NP_CLUSTER_NAME"],
            access_token_cookie_name=self._environ["NP_AUTH_ACCESS_TOKEN_COOKIE_NAME"],
        )

    def create_grafana_proxy(self) -> GrafanaProxyConfig:
        return GrafanaProxyConfig(
            server=self._create_server(),
            public_server=self._create_grafana_public_server(),
            grafana_server=self._create_grafana_server(),
            platform_auth=self._create_platform_auth(),
            platform_api=self._create_platform_api(),
            cluster_name=self._environ["NP_CLUSTER_NAME"],
            access_token_cookie_name=self._environ["NP_AUTH_ACCESS_TOKEN_COOKIE_NAME"],
        )

    def _create_server(self) -> ServerConfig:
        return ServerConfig(
            scheme=self._environ.get("NP_REPORTS_API_SCHEME", ServerConfig.scheme),
            host=self._environ.get("NP_REPORTS_API_HOST", ServerConfig.host),
            port=int(self._environ.get("NP_REPORTS_API_PORT", ServerConfig.port)),
        )

    def _create_prometheus_server(self) -> ServerConfig:
        return ServerConfig(
            scheme=self._environ.get("NP_PROMETHEUS_SCHEME", ServerConfig.scheme),
            host=self._environ["NP_PROMETHEUS_HOST"],
            port=int(self._environ["NP_PROMETHEUS_PORT"]),
        )

    def _create_grafana_server(self) -> ServerConfig:
        return ServerConfig(
            scheme=self._environ.get("NP_GRAFANA_SCHEME", ServerConfig.scheme),
            host=self._environ["NP_GRAFANA_HOST"],
            port=int(self._environ["NP_GRAFANA_PORT"]),
        )

    def _create_grafana_public_server(self) -> ServerConfig:
        return ServerConfig(
            scheme=self._environ.get("NP_GRAFANA_PUBLIC_SCHEME", ServerConfig.scheme),
            host=self._environ["NP_GRAFANA_PUBLIC_HOST"],
            port=int(self._environ["NP_GRAFANA_PUBLIC_PORT"]),
        )

    def _create_platform_auth(self) -> PlatformAuthConfig:
        return PlatformAuthConfig(
            url=URL(self._environ["NP_AUTH_URL"]), token=self._environ["NP_AUTH_TOKEN"]
        )

    def _create_platform_api(self) -> PlatformApiConfig:
        return PlatformApiConfig(
            url=URL(self._environ["NP_API_URL"]), token=self._environ["NP_AUTH_TOKEN"]
        )

from __future__ import annotations

import enum
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pydantic
from aiohttp.client import DEFAULT_TIMEOUT, ClientTimeout
from pydantic_settings import BaseSettings, SettingsConfigDict
from yarl import URL


class Label:
    class Key(str): ...

    NEURO_NODE_POOL_KEY = Key("platform.neuromation.io/nodepool")
    NEURO_PREEMPTIBLE_KEY = Key("platform.neuromation.io/preemptible")
    NEURO_PRESET_KEY = Key("platform.neuromation.io/preset")
    NEURO_USER_KEY = Key("platform.neuromation.io/user")
    NEURO_JOB_KEY = Key("platform.neuromation.io/job")

    APOLO_ORG_KEY = Key("platform.apolo.us/org")
    APOLO_PROJECT_KEY = Key("platform.apolo.us/project")
    APOLO_USER_KEY = Key("platform.apolo.us/user")
    APOLO_PRESET_KEY = Key("platform.apolo.us/preset")
    APOLO_APP_INSTANCE_NAME_KEY = Key("platform.apolo.us/app-instance-name")

    FAILURE_DOMAIN_REGION_KEY = Key("failure-domain.beta.kubernetes.io/region")
    FAILURE_DOMAIN_ZONE_KEY = Key("failure-domain.beta.kubernetes.io/zone")
    TOPOLOGY_REGION_KEY = Key("topology.kubernetes.io/region")
    TOPOLOGY_ZONE_KEY = Key("topology.kubernetes.io/zone")
    NODE_INSTANCE_TYPE_KEY = Key("node.kubernetes.io/instance-type")
    INSTANCE_TYPE_KEY = Key("beta.kubernetes.io/instance-type")


class PrometheusLabelMeta(type):
    _UNSUPPORT_CHARS_RE = re.compile(r"[^a-zA-Z0-9_]")

    def __new__(cls, *args: Any, **kwargs: Any) -> type[PrometheusLabel]:
        instance = super().__new__(cls, *args, **kwargs)
        for name in dir(instance):
            value = getattr(instance, name)
            if isinstance(value, Label.Key):
                value = "label_" + cls._UNSUPPORT_CHARS_RE.sub("_", value)
                setattr(instance, name, Label.Key(value))
        return instance


class PrometheusLabel(Label, metaclass=PrometheusLabelMeta):
    pass


class KubeClientAuthType(enum.Enum):
    NONE = "none"
    TOKEN = "token"
    CERTIFICATE = "certificate"


@dataclass(frozen=True)
class KubeConfig:
    url: URL
    cert_authority_path: str | None = None
    cert_authority_data_pem: str | None = field(repr=False, default=None)
    auth_type: KubeClientAuthType = KubeClientAuthType.NONE
    client_cert_path: str | None = None
    client_key_path: str | None = None
    token: str | None = field(repr=False, default=None)
    token_path: str | None = None
    token_update_interval_s: int = 300
    conn_timeout_s: int = 300
    read_timeout_s: int = 100
    conn_pool_size: int = 100
    conn_keep_alive_timeout_s: int = 15


@dataclass(frozen=True)
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8080


@dataclass(frozen=True)
class PlatformAuthConfig:
    url: URL | None
    token: str = field(repr=False)


@dataclass(frozen=True)
class PlatformServiceConfig:
    url: URL
    token: str = field(repr=False)


@dataclass(frozen=True)
class PlatformAppsConfig:
    url: URL
    token: str = field(repr=False)


@dataclass(frozen=True)
class MetricsExporterConfig:
    server: ServerConfig
    kube: KubeConfig
    platform_config: PlatformServiceConfig
    platform_api: PlatformServiceConfig
    cluster_name: str
    cloud_provider: str = ""
    region: str = ""
    gcp_service_account_key_path: Path | None = None
    azure_prices_url: URL = URL("https://prices.azure.com")


class MetricsApiConfig(BaseSettings):
    model_config = SettingsConfigDict(env_nested_delimiter="__")

    class Server(pydantic.BaseModel):
        host: str = "0.0.0.0"
        port: int = 8080

    class PlatformConfig(pydantic.BaseModel):
        auth_url: pydantic.HttpUrl | None
        config_url: pydantic.HttpUrl | None
        token: str = pydantic.Field(repr=False)

        @property
        def auth_yarl_url(self) -> URL | None:
            return URL(str(self.auth_url)) if self.auth_url else None

        @property
        def config_yarl_url(self) -> URL:
            return URL(str(self.config_url))

    server: Server = Server()
    platform: PlatformConfig
    prometheus_url: pydantic.HttpUrl
    cluster_name: str

    @property
    def prometheus_yarl_url(self) -> URL:
        return URL(str(self.prometheus_url))


@dataclass(frozen=True)
class PrometheusProxyConfig:
    server: ServerConfig
    prometheus_url: URL
    platform_auth: PlatformAuthConfig
    platform_api: PlatformServiceConfig
    cluster_name: str
    access_token_cookie_names: Sequence[str]
    timeout: ClientTimeout = DEFAULT_TIMEOUT


@dataclass(frozen=True)
class GrafanaProxyConfig:
    server: ServerConfig
    grafana_url: URL
    platform_auth: PlatformAuthConfig
    platform_api: PlatformServiceConfig
    platform_apps: PlatformAppsConfig
    cluster_name: str
    access_token_cookie_names: Sequence[str]
    timeout: ClientTimeout = DEFAULT_TIMEOUT


class EnvironConfigFactory:
    def __init__(self, environ: dict[str, str] | None = None) -> None:
        self._environ = environ or os.environ

    def _get_url(self, name: str) -> URL | None:
        value = self._environ[name]
        return None if value == "-" else URL(value)

    def create_metrics(self) -> MetricsExporterConfig:
        gcp_service_account_key_path = (
            MetricsExporterConfig.gcp_service_account_key_path
        )
        if self._environ.get("NP_GCP_SERVICE_ACCOUNT_KEY_PATH"):
            gcp_service_account_key_path = Path(
                self._environ["NP_GCP_SERVICE_ACCOUNT_KEY_PATH"]
            )
        return MetricsExporterConfig(
            server=self._create_server(),
            kube=self.create_kube(),
            platform_config=self._create_platform_config(),
            platform_api=self._create_platform_api(),
            cluster_name=self._environ["NP_CLUSTER_NAME"],
            cloud_provider=self._environ.get(
                "NP_CLOUD_PROVIDER", MetricsExporterConfig.cloud_provider
            ),
            region=self._environ.get("NP_REGION", MetricsExporterConfig.region),
            gcp_service_account_key_path=gcp_service_account_key_path,
            azure_prices_url=URL(
                self._environ.get(
                    "NP_AZURE_PRICES_URL", str(MetricsExporterConfig.azure_prices_url)
                )
            ),
        )

    def create_prometheus_proxy(self) -> PrometheusProxyConfig:
        return PrometheusProxyConfig(
            server=self._create_server(),
            prometheus_url=URL(self._environ["PROMETHEUS_URL"]),
            platform_auth=self._create_platform_auth(),
            platform_api=self._create_platform_api(),
            cluster_name=self._environ["NP_CLUSTER_NAME"],
            access_token_cookie_names=self._environ[
                "NP_AUTH_ACCESS_TOKEN_COOKIE_NAMES"
            ].split(","),
        )

    def create_grafana_proxy(self) -> GrafanaProxyConfig:
        return GrafanaProxyConfig(
            server=self._create_server(),
            grafana_url=URL(self._environ["GRAFANA_URL"]),
            platform_auth=self._create_platform_auth(),
            platform_api=self._create_platform_api(),
            platform_apps=self._create_platform_apps_config(),
            cluster_name=self._environ["NP_CLUSTER_NAME"],
            access_token_cookie_names=self._environ[
                "NP_AUTH_ACCESS_TOKEN_COOKIE_NAMES"
            ].split(","),
        )

    def _create_server(self) -> ServerConfig:
        return ServerConfig(
            host=self._environ.get("SERVER_HOST", ServerConfig.host),
            port=int(self._environ.get("SERVER_PORT", ServerConfig.port)),
        )

    def _create_platform_auth(self) -> PlatformAuthConfig:
        return PlatformAuthConfig(
            url=self._get_url("NP_AUTH_URL"), token=self._environ["NP_TOKEN"]
        )

    def _create_platform_api(self) -> PlatformServiceConfig:
        return PlatformServiceConfig(
            url=URL(self._environ["NP_API_URL"]), token=self._environ["NP_TOKEN"]
        )

    def _create_platform_apps_config(self) -> PlatformAppsConfig:
        return PlatformAppsConfig(
            url=URL(self._environ["NP_APPS_URL"]),
            token=self._environ["NP_TOKEN"],
        )

    def _create_platform_config(self) -> PlatformServiceConfig:
        return PlatformServiceConfig(
            url=URL(self._environ["NP_CONFIG_URL"]), token=self._environ["NP_TOKEN"]
        )

    def create_kube(self) -> KubeConfig:
        return KubeConfig(
            url=URL(self._environ["NP_KUBE_URL"]),
            auth_type=KubeClientAuthType(
                self._environ.get("NP_KUBE_AUTH_TYPE", KubeConfig.auth_type.value)
            ),
            token=self._environ.get("NP_KUBE_TOKEN"),
            token_path=self._environ.get("NP_KUBE_TOKEN_PATH"),
            cert_authority_data_pem=self._environ.get("NP_KUBE_CERT_AUTHORITY_DATA"),
            cert_authority_path=self._environ.get("NP_KUBE_CERT_AUTHORITY_PATH"),
            client_cert_path=self._environ.get("NP_KUBE_CLIENT_CERT_PATH"),
            client_key_path=self._environ.get("NP_KUBE_CLIENT_KEY_PATH"),
            conn_timeout_s=int(
                self._environ.get("NP_KUBE_CONN_TIMEOUT", KubeConfig.conn_timeout_s)
            ),
            read_timeout_s=int(
                self._environ.get("NP_KUBE_READ_TIMEOUT", KubeConfig.read_timeout_s)
            ),
            conn_pool_size=int(
                self._environ.get("NP_KUBE_CONN_POOL_SIZE", KubeConfig.conn_pool_size)
            ),
            conn_keep_alive_timeout_s=int(
                self._environ.get(
                    "NP_KUBE_CONN_KEEP_ALIVE_TIMEOUT",
                    KubeConfig.conn_keep_alive_timeout_s,
                )
            ),
        )

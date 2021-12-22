from __future__ import annotations

import enum
import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from aiohttp.client import DEFAULT_TIMEOUT, ClientTimeout
from yarl import URL

logger = logging.getLogger(__name__)


class KubeClientAuthType(enum.Enum):
    NONE = "none"
    TOKEN = "token"
    CERTIFICATE = "certificate"


@dataclass(frozen=True)
class KubeConfig:
    url: URL
    cert_authority_path: str | None = None
    cert_authority_data_pem: str | None = None
    auth_type: KubeClientAuthType = KubeClientAuthType.NONE
    client_cert_path: str | None = None
    client_key_path: str | None = None
    token: str | None = None
    token_path: str | None = None
    conn_timeout_s: int = 300
    read_timeout_s: int = 100
    conn_pool_size: int = 100
    conn_keep_alive_timeout_s: int = 15


@dataclass(frozen=True)
class ServerConfig:
    scheme: str = "http"
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
class ZipkinConfig:
    url: URL
    app_name: str
    sample_rate: float = 0


@dataclass(frozen=True)
class SentryConfig:
    dsn: URL
    cluster_name: str
    app_name: str
    sample_rate: float = 0


@dataclass(frozen=True)
class MetricsConfig:
    server: ServerConfig
    kube: KubeConfig
    platform_config: PlatformServiceConfig
    cluster_name: str
    node_name: str
    cloud_provider: str = ""
    region: str = ""
    gcp_service_account_key_path: Path | None = None
    azure_prices_url: URL = URL("https://prices.azure.com")
    jobs_namespace: str = ""
    node_pool_label: str = "platform.neuromation.io/nodepool"
    node_preemptible_label: str = "platform.neuromation.io/preemptible"
    job_label: str = "platform.neuromation.io/job"
    preset_label: str = "platform.neuromation.io/preset"

    zipkin: ZipkinConfig | None = None
    sentry: SentryConfig | None = None


@dataclass(frozen=True)
class PrometheusProxyConfig:
    server: ServerConfig
    prometheus_server: ServerConfig
    platform_auth: PlatformAuthConfig
    platform_api: PlatformServiceConfig
    cluster_name: str
    access_token_cookie_names: Sequence[str]
    timeout: ClientTimeout = DEFAULT_TIMEOUT

    zipkin: ZipkinConfig | None = None
    sentry: SentryConfig | None = None


@dataclass(frozen=True)
class GrafanaProxyConfig:
    server: ServerConfig
    grafana_server: ServerConfig
    platform_auth: PlatformAuthConfig
    platform_api: PlatformServiceConfig
    cluster_name: str
    access_token_cookie_names: Sequence[str]
    timeout: ClientTimeout = DEFAULT_TIMEOUT

    zipkin: ZipkinConfig | None = None
    sentry: SentryConfig | None = None


class EnvironConfigFactory:
    def __init__(self, environ: dict[str, str] | None = None) -> None:
        self._environ = environ or os.environ

    def _get_url(self, name: str) -> URL | None:
        value = self._environ[name]
        if value == "-":
            return None
        else:
            return URL(value)

    def create_metrics(self) -> MetricsConfig:
        gcp_service_account_key_path = MetricsConfig.gcp_service_account_key_path
        if self._environ.get("NP_GCP_SERVICE_ACCOUNT_KEY_PATH"):
            gcp_service_account_key_path = Path(
                self._environ["NP_GCP_SERVICE_ACCOUNT_KEY_PATH"]
            )
        return MetricsConfig(
            server=ServerConfig(
                scheme=self._environ.get("NP_METRICS_API_SCHEME", ServerConfig.scheme),
                host=self._environ.get("NP_METRICS_API_HOST", ServerConfig.host),
                port=int(self._environ.get("NP_METRICS_API_PORT", ServerConfig.port)),
            ),
            kube=self.create_kube(),
            platform_config=self._create_platform_config(),
            cluster_name=self._environ["NP_CLUSTER_NAME"],
            node_name=self._environ["NP_NODE_NAME"],
            cloud_provider=self._environ.get(
                "NP_CLOUD_PROVIDER", MetricsConfig.cloud_provider
            ),
            region=self._environ.get("NP_REGION", MetricsConfig.region),
            gcp_service_account_key_path=gcp_service_account_key_path,
            azure_prices_url=URL(
                self._environ.get(
                    "NP_AZURE_PRICES_URL", str(MetricsConfig.azure_prices_url)
                )
            ),
            jobs_namespace=self._environ.get(
                "NP_JOBS_NAMESPACE", MetricsConfig.jobs_namespace
            ),
            node_pool_label=self._environ.get(
                "NP_NODE_POOL_LABEL", MetricsConfig.node_pool_label
            ),
            node_preemptible_label=self._environ.get(
                "NP_NODE_PREEMPTIBLE_LABEL", MetricsConfig.node_preemptible_label
            ),
            job_label=self._environ.get("NP_JOB_LABEL", MetricsConfig.job_label),
            preset_label=self._environ.get(
                "NP_PRESET_LABEL", MetricsConfig.preset_label
            ),
            zipkin=self.create_zipkin(default_app_name="platform-metrics-exporter"),
            sentry=self.create_sentry(default_app_name="platform-metrics-exporter"),
        )

    def create_prometheus_proxy(self) -> PrometheusProxyConfig:
        return PrometheusProxyConfig(
            server=self._create_server(),
            prometheus_server=self._create_prometheus_server(),
            platform_auth=self._create_platform_auth(),
            platform_api=self._create_platform_api(),
            cluster_name=self._environ["NP_CLUSTER_NAME"],
            access_token_cookie_names=self._environ[
                "NP_AUTH_ACCESS_TOKEN_COOKIE_NAMES"
            ].split(","),
            zipkin=self.create_zipkin(default_app_name="platform-prometheus-proxy"),
            sentry=self.create_sentry(default_app_name="platform-prometheus-proxy"),
        )

    def create_grafana_proxy(self) -> GrafanaProxyConfig:
        return GrafanaProxyConfig(
            server=self._create_server(),
            grafana_server=self._create_grafana_server(),
            platform_auth=self._create_platform_auth(),
            platform_api=self._create_platform_api(),
            cluster_name=self._environ["NP_CLUSTER_NAME"],
            access_token_cookie_names=self._environ[
                "NP_AUTH_ACCESS_TOKEN_COOKIE_NAMES"
            ].split(","),
            zipkin=self.create_zipkin(default_app_name="platform-grafana-proxy"),
            sentry=self.create_sentry(default_app_name="platform-grafana-proxy"),
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

    def _create_platform_auth(self) -> PlatformAuthConfig:
        return PlatformAuthConfig(
            url=self._get_url("NP_AUTH_URL"), token=self._environ["NP_AUTH_TOKEN"]
        )

    def _create_platform_api(self) -> PlatformServiceConfig:
        return PlatformServiceConfig(
            url=URL(self._environ["NP_API_URL"]), token=self._environ["NP_AUTH_TOKEN"]
        )

    def _create_platform_config(self) -> PlatformServiceConfig:
        return PlatformServiceConfig(
            url=URL(self._environ["NP_CONFIG_URL"]),
            token=self._environ["NP_CONFIG_TOKEN"],
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

    def create_zipkin(self, default_app_name: str) -> ZipkinConfig | None:
        if "NP_ZIPKIN_URL" not in self._environ:
            return None

        url = URL(self._environ["NP_ZIPKIN_URL"])
        app_name = self._environ.get("NP_ZIPKIN_APP_NAME", default_app_name)
        sample_rate = float(
            self._environ.get("NP_ZIPKIN_SAMPLE_RATE", ZipkinConfig.sample_rate)
        )
        return ZipkinConfig(url=url, app_name=app_name, sample_rate=sample_rate)

    def create_sentry(self, default_app_name: str) -> SentryConfig | None:
        if "NP_SENTRY_DSN" not in self._environ:
            return None

        return SentryConfig(
            dsn=URL(self._environ["NP_SENTRY_DSN"]),
            cluster_name=self._environ["NP_SENTRY_CLUSTER_NAME"],
            app_name=self._environ.get("NP_SENTRY_APP_NAME", default_app_name),
            sample_rate=float(
                self._environ.get("NP_SENTRY_SAMPLE_RATE", SentryConfig.sample_rate)
            ),
        )

import os
from collections.abc import Iterator
from pathlib import Path

import pydantic
import pytest
from yarl import URL

from platform_reports.config import (
    EnvironConfigFactory,
    GrafanaProxyConfig,
    KubeClientAuthType,
    KubeConfig,
    MetricsApiConfig,
    MetricsExporterConfig,
    PlatformAuthConfig,
    PlatformServiceConfig,
    PrometheusProxyConfig,
    ServerConfig,
)


@pytest.fixture(autouse=True)
def _reset_os_environ() -> Iterator[None]:
    environ = os.environ.copy()

    yield

    os.environ.clear()
    os.environ.update(environ)


class TestEnvironConfigFactory:
    def test_create_metrics_exporter_defaults(self) -> None:
        env = {
            "NP_CONFIG_URL": "http://dev.neu.ro",
            "NP_API_URL": "http://dev.neu.ro/api/v1",
            "NP_TOKEN": "token",
            "NP_CLUSTER_NAME": "default",
            "NP_NODE_NAME": "node",
            "NP_KUBE_URL": "https://kubernetes.default.svc",
        }

        result = EnvironConfigFactory(env).create_metrics()

        assert result == MetricsExporterConfig(
            server=ServerConfig(),
            platform_config=PlatformServiceConfig(
                url=URL("http://dev.neu.ro"), token="token"
            ),
            platform_api=PlatformServiceConfig(
                url=URL("http://dev.neu.ro/api/v1"), token="token"
            ),
            kube=KubeConfig(
                url=URL("https://kubernetes.default.svc"),
                auth_type=KubeClientAuthType.NONE,
            ),
            cluster_name="default",
            node_name="node",
        )

    def test_create_metrics__exporter_custom(self) -> None:
        env = {
            "SERVER_HOST": "metrics",
            "SERVER_PORT": "9500",
            "NP_CONFIG_URL": "http://dev.neu.ro",
            "NP_API_URL": "http://dev.neu.ro/api/v1",
            "NP_TOKEN": "token",
            "NP_CLUSTER_NAME": "default",
            "NP_NODE_NAME": "node",
            "NP_CLOUD_PROVIDER": "aws",
            "NP_REGION": "us-east-1",
            "NP_GCP_SERVICE_ACCOUNT_KEY_PATH": "sa.json",
            "NP_AZURE_PRICES_URL": "https://azure-prices",
            "NP_KUBE_URL": "https://kubernetes.default.svc",
        }

        result = EnvironConfigFactory(env).create_metrics()

        assert result == MetricsExporterConfig(
            server=ServerConfig(host="metrics", port=9500),
            platform_config=PlatformServiceConfig(
                url=URL("http://dev.neu.ro"), token="token"
            ),
            platform_api=PlatformServiceConfig(
                url=URL("http://dev.neu.ro/api/v1"), token="token"
            ),
            kube=KubeConfig(
                url=URL("https://kubernetes.default.svc"),
                auth_type=KubeClientAuthType.NONE,
            ),
            cluster_name="default",
            node_name="node",
            cloud_provider="aws",
            region="us-east-1",
            gcp_service_account_key_path=Path("sa.json"),
            azure_prices_url=URL("https://azure-prices"),
        )

    def test_create_prometheus_proxy_defaults(self) -> None:
        env = {
            "NP_CLUSTER_NAME": "default",
            "NP_AUTH_ACCESS_TOKEN_COOKIE_NAMES": "sat,dat",
            "PROMETHEUS_URL": "http://prometheus:9090",
            "NP_AUTH_URL": "-",
            "NP_TOKEN": "token",
            "NP_API_URL": "https://dev.neu.ro/api/v1",
        }

        result = EnvironConfigFactory(env).create_prometheus_proxy()

        assert result == PrometheusProxyConfig(
            server=ServerConfig(),
            cluster_name="default",
            access_token_cookie_names=["sat", "dat"],
            prometheus_url=URL("http://prometheus:9090"),
            platform_auth=PlatformAuthConfig(url=None, token="token"),
            platform_api=PlatformServiceConfig(
                url=URL("https://dev.neu.ro/api/v1"), token="token"
            ),
        )

    def test_create_prometheus_proxy_custom(self) -> None:
        env = {
            "NP_CLUSTER_NAME": "default",
            "NP_AUTH_ACCESS_TOKEN_COOKIE_NAMES": "sat,dat",
            "SERVER_HOST": "platform-prometheus-proxy",
            "SERVER_PORT": "80",
            "PROMETHEUS_URL": "http://prometheus:9090",
            "NP_AUTH_URL": "https://dev.neu.ro",
            "NP_TOKEN": "token",
            "NP_API_URL": "https://dev.neu.ro/api/v1",
        }

        result = EnvironConfigFactory(env).create_prometheus_proxy()

        assert result == PrometheusProxyConfig(
            cluster_name="default",
            access_token_cookie_names=["sat", "dat"],
            server=ServerConfig(host="platform-prometheus-proxy", port=80),
            prometheus_url=URL("http://prometheus:9090"),
            platform_auth=PlatformAuthConfig(
                url=URL("https://dev.neu.ro"), token="token"
            ),
            platform_api=PlatformServiceConfig(
                url=URL("https://dev.neu.ro/api/v1"), token="token"
            ),
        )

    def test_create_grafana_proxy_defaults(self) -> None:
        env = {
            "NP_CLUSTER_NAME": "default",
            "NP_AUTH_ACCESS_TOKEN_COOKIE_NAMES": "sat,dat",
            "GRAFANA_URL": "http://grafana:3000",
            "NP_AUTH_URL": "-",
            "NP_TOKEN": "token",
            "NP_API_URL": "https://dev.neu.ro/api/v1",
        }

        result = EnvironConfigFactory(env).create_grafana_proxy()

        assert result == GrafanaProxyConfig(
            cluster_name="default",
            access_token_cookie_names=["sat", "dat"],
            server=ServerConfig(),
            grafana_url=URL("http://grafana:3000"),
            platform_auth=PlatformAuthConfig(url=None, token="token"),
            platform_api=PlatformServiceConfig(
                url=URL("https://dev.neu.ro/api/v1"), token="token"
            ),
        )

    def test_create_grafana_proxy_custom(self) -> None:
        env = {
            "NP_CLUSTER_NAME": "default",
            "NP_AUTH_ACCESS_TOKEN_COOKIE_NAMES": "sat,dat",
            "SERVER_HOST": "platform-grafana-proxy",
            "SERVER_PORT": "80",
            "GRAFANA_URL": "http://grafana:3000",
            "NP_AUTH_URL": "https://dev.neu.ro",
            "NP_TOKEN": "token",
            "NP_API_URL": "https://dev.neu.ro/api/v1",
        }

        result = EnvironConfigFactory(env).create_grafana_proxy()

        assert result == GrafanaProxyConfig(
            cluster_name="default",
            access_token_cookie_names=["sat", "dat"],
            server=ServerConfig(host="platform-grafana-proxy", port=80),
            grafana_url=URL("http://grafana:3000"),
            platform_auth=PlatformAuthConfig(
                url=URL("https://dev.neu.ro"), token="token"
            ),
            platform_api=PlatformServiceConfig(
                url=URL("https://dev.neu.ro/api/v1"), token="token"
            ),
        )

    def test_create_kube(self) -> None:
        env = {
            "NP_KUBE_URL": "https://kubernetes.default.svc",
            "NP_KUBE_AUTH_TYPE": "token",
            "NP_KUBE_TOKEN": "k8s-token",
            "NP_KUBE_TOKEN_PATH": "k8s-token-path",
            "NP_KUBE_CERT_AUTHORITY_DATA": "k8s-ca-data",
            "NP_KUBE_CERT_AUTHORITY_PATH": "k8s-ca-path",
            "NP_KUBE_CLIENT_CERT_PATH": "k8s-client-cert-path",
            "NP_KUBE_CLIENT_KEY_PATH": "k8s-client-key-path",
            "NP_KUBE_CONN_TIMEOUT": "100",
            "NP_KUBE_READ_TIMEOUT": "200",
            "NP_KUBE_CONN_POOL_SIZE": "300",
            "NP_KUBE_CONN_KEEP_ALIVE_TIMEOUT": "400",
        }
        result = EnvironConfigFactory(env).create_kube()

        assert result == KubeConfig(
            url=URL("https://kubernetes.default.svc"),
            auth_type=KubeClientAuthType.TOKEN,
            token="k8s-token",
            token_path="k8s-token-path",
            cert_authority_data_pem="k8s-ca-data",
            cert_authority_path="k8s-ca-path",
            client_cert_path="k8s-client-cert-path",
            client_key_path="k8s-client-key-path",
            conn_timeout_s=100,
            read_timeout_s=200,
            conn_pool_size=300,
            conn_keep_alive_timeout_s=400,
        )

    def test_create_metrics_api(self) -> None:
        os.environ["PLATFORM_AUTH__URL"] = "http://platform-auth.platform"
        os.environ["PLATFORM_AUTH__TOKEN"] = "test-token"
        os.environ["PROMETHEUS_URL"] = "http://prometheus.platform"
        os.environ["CLUSTER_NAME"] = "test-cluster"

        config = MetricsApiConfig()  # type: ignore

        assert config == MetricsApiConfig(
            platform_auth=MetricsApiConfig.PlatformAuth(
                url=pydantic.HttpUrl("http://platform-auth.platform"),
                token="test-token",
            ),
            prometheus_url=pydantic.HttpUrl("http://prometheus.platform"),
            cluster_name="test-cluster",
        )

        os.environ["SERVER__HOST"] = "127.0.0.1"
        os.environ["SERVER__PORT"] = "9090"

        config = MetricsApiConfig()  # type: ignore

        assert config.server == MetricsApiConfig.Server(host="127.0.0.1", port=9090)

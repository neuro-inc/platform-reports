import tempfile
from pathlib import Path
from typing import Iterator

import pytest
from yarl import URL

from platform_reports.config import (
    EnvironConfigFactory,
    GrafanaProxyConfig,
    MetricsConfig,
    PlatformApiConfig,
    PlatformAuthConfig,
    PrometheusProxyConfig,
    ServerConfig,
)


class TestEnvironConfigFactory:
    @pytest.fixture
    def temp_file(self) -> Iterator[Path]:
        temp_file_path = Path(tempfile.mktemp())
        temp_file_path.touch()
        yield temp_file_path
        temp_file_path.unlink(missing_ok=True)

    def test_create_metrics_defaults(self) -> None:
        env = {
            "NP_HOST_NAME": "host",
        }

        result = EnvironConfigFactory(env).create_metrics()

        assert result == MetricsConfig(server=ServerConfig(), host_name="host")

    def test_create_metrics_custom(self, temp_file: Path) -> None:
        temp_file.write_text("p2.xlarge")
        env = {
            "NP_METRICS_API_SCHEME": "http",
            "NP_METRICS_API_HOST": "metrics",
            "NP_METRICS_API_PORT": "9500",
            "NP_HOST_NAME": "host",
            "NP_INSTANCE_TYPE_PATH": str(temp_file),
            "NP_CLOUD_PROVIDER": "aws",
            "NP_REGION": "us-east-1",
        }

        result = EnvironConfigFactory(env).create_metrics()

        assert result == MetricsConfig(
            server=ServerConfig(scheme="http", host="metrics", port=9500),
            host_name="host",
            instance_type="p2.xlarge",
            cloud_provider="aws",
            region="us-east-1",
        )

    def test_create_prometheus_proxy_defaults(self) -> None:
        env = {
            "NP_CLUSTER_NAME": "default",
            "NP_AUTH_ACCESS_TOKEN_COOKIE_NAME": "dat",
            "NP_PROMETHEUS_HOST": "prometheus",
            "NP_PROMETHEUS_PORT": "9090",
            "NP_AUTH_URL": "https://dev.neu.ro",
            "NP_AUTH_TOKEN": "token",
            "NP_API_URL": "https://dev.neu.ro/api/v1",
        }

        result = EnvironConfigFactory(env).create_prometheus_proxy()

        assert result == PrometheusProxyConfig(
            cluster_name="default",
            access_token_cookie_name="dat",
            server=ServerConfig(),
            prometheus_server=ServerConfig(host="prometheus", port=9090),
            platform_auth=PlatformAuthConfig(
                url=URL("https://dev.neu.ro"), token="token"
            ),
            platform_api=PlatformApiConfig(
                url=URL("https://dev.neu.ro/api/v1"), token="token"
            ),
        )

    def test_create_prometheus_proxy_custom(self) -> None:
        env = {
            "NP_CLUSTER_NAME": "default",
            "NP_AUTH_ACCESS_TOKEN_COOKIE_NAME": "dat",
            "NP_REPORTS_API_SCHEME": "https",
            "NP_REPORTS_API_HOST": "platform-reports",
            "NP_REPORTS_API_PORT": "80",
            "NP_PROMETHEUS_SCHEME": "https",
            "NP_PROMETHEUS_HOST": "prometheus",
            "NP_PROMETHEUS_PORT": "9090",
            "NP_AUTH_URL": "https://dev.neu.ro",
            "NP_AUTH_TOKEN": "token",
            "NP_API_URL": "https://dev.neu.ro/api/v1",
        }

        result = EnvironConfigFactory(env).create_prometheus_proxy()

        assert result == PrometheusProxyConfig(
            cluster_name="default",
            access_token_cookie_name="dat",
            server=ServerConfig(scheme="https", host="platform-reports", port=80),
            prometheus_server=ServerConfig(
                scheme="https", host="prometheus", port=9090
            ),
            platform_auth=PlatformAuthConfig(
                url=URL("https://dev.neu.ro"), token="token"
            ),
            platform_api=PlatformApiConfig(
                url=URL("https://dev.neu.ro/api/v1"), token="token"
            ),
        )

    def test_create_grafana_proxy_defaults(self) -> None:
        env = {
            "NP_CLUSTER_NAME": "default",
            "NP_AUTH_ACCESS_TOKEN_COOKIE_NAME": "dat",
            "NP_GRAFANA_HOST": "grafana",
            "NP_GRAFANA_PORT": "3000",
            "NP_GRAFANA_PUBLIC_HOST": "grafana-public",
            "NP_GRAFANA_PUBLIC_PORT": "80",
            "NP_AUTH_URL": "https://dev.neu.ro",
            "NP_AUTH_TOKEN": "token",
            "NP_API_URL": "https://dev.neu.ro/api/v1",
        }

        result = EnvironConfigFactory(env).create_grafana_proxy()

        assert result == GrafanaProxyConfig(
            cluster_name="default",
            access_token_cookie_name="dat",
            server=ServerConfig(),
            public_server=ServerConfig(host="grafana-public", port=80),
            grafana_server=ServerConfig(host="grafana", port=3000),
            platform_auth=PlatformAuthConfig(
                url=URL("https://dev.neu.ro"), token="token"
            ),
            platform_api=PlatformApiConfig(
                url=URL("https://dev.neu.ro/api/v1"), token="token"
            ),
        )

    def test_create_grafana_proxy_custom(self) -> None:
        env = {
            "NP_CLUSTER_NAME": "default",
            "NP_AUTH_ACCESS_TOKEN_COOKIE_NAME": "dat",
            "NP_REPORTS_API_SCHEME": "https",
            "NP_REPORTS_API_HOST": "platform-reports",
            "NP_REPORTS_API_PORT": "80",
            "NP_GRAFANA_SCHEME": "https",
            "NP_GRAFANA_HOST": "grafana",
            "NP_GRAFANA_PORT": "3000",
            "NP_GRAFANA_PUBLIC_SCHEME": "https",
            "NP_GRAFANA_PUBLIC_HOST": "grafana-public",
            "NP_GRAFANA_PUBLIC_PORT": "80",
            "NP_AUTH_URL": "https://dev.neu.ro",
            "NP_AUTH_TOKEN": "token",
            "NP_API_URL": "https://dev.neu.ro/api/v1",
        }

        result = EnvironConfigFactory(env).create_grafana_proxy()

        assert result == GrafanaProxyConfig(
            cluster_name="default",
            access_token_cookie_name="dat",
            server=ServerConfig(scheme="https", host="platform-reports", port=80),
            public_server=ServerConfig(scheme="https", host="grafana-public", port=80),
            grafana_server=ServerConfig(scheme="https", host="grafana", port=3000),
            platform_auth=PlatformAuthConfig(
                url=URL("https://dev.neu.ro"), token="token"
            ),
            platform_api=PlatformApiConfig(
                url=URL("https://dev.neu.ro/api/v1"), token="token"
            ),
        )

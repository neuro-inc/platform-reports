from yarl import URL

from platform_reports.config import (
    EnvironConfigFactory,
    GrafanaProxyConfig,
    KubeClientAuthType,
    KubeConfig,
    MetricsConfig,
    PlatformApiConfig,
    PlatformAuthConfig,
    PrometheusProxyConfig,
    ServerConfig,
)


class TestEnvironConfigFactory:
    def test_create_metrics_defaults(self) -> None:
        env = {
            "NP_NODE_NAME": "node",
        }

        result = EnvironConfigFactory(env).create_metrics()

        assert result == MetricsConfig(
            server=ServerConfig(),
            kube=KubeConfig(
                auth_type=KubeClientAuthType.TOKEN,
                url=URL("https://kubernetes.default.svc"),
                token_path="/var/run/secrets/kubernetes.io/serviceaccount/token",
                cert_authority_path=(
                    "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
                ),
            ),
            node_name="node",
        )

    def test_create_metrics_custom(self) -> None:
        env = {
            "NP_METRICS_API_SCHEME": "http",
            "NP_METRICS_API_HOST": "metrics",
            "NP_METRICS_API_PORT": "9500",
            "NP_NODE_NAME": "node",
            "NP_CLOUD_PROVIDER": "aws",
            "NP_REGION": "us-east-1",
        }

        result = EnvironConfigFactory(env).create_metrics()

        assert result == MetricsConfig(
            server=ServerConfig(scheme="http", host="metrics", port=9500),
            kube=KubeConfig(
                auth_type=KubeClientAuthType.TOKEN,
                url=URL("https://kubernetes.default.svc"),
                token_path="/var/run/secrets/kubernetes.io/serviceaccount/token",
                cert_authority_path=(
                    "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
                ),
            ),
            node_name="node",
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

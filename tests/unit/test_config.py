from pathlib import Path

from yarl import URL

from platform_reports.config import (
    EnvironConfigFactory,
    GrafanaProxyConfig,
    KubeClientAuthType,
    KubeConfig,
    MetricsConfig,
    PlatformServiceConfig,
    PrometheusProxyConfig,
    SentryConfig,
    ServerConfig,
    ZipkinConfig,
)


class TestEnvironConfigFactory:
    def test_create_metrics_defaults(self) -> None:
        env = {
            "NP_CONFIG_URL": "http://dev.neu.ro",
            "NP_CONFIG_TOKEN": "token",
            "NP_CLUSTER_NAME": "default",
            "NP_NODE_NAME": "node",
        }

        result = EnvironConfigFactory(env).create_metrics()

        assert result == MetricsConfig(
            server=ServerConfig(),
            platform_config=PlatformServiceConfig(
                url=URL("http://dev.neu.ro"), token="token"
            ),
            kube=KubeConfig(
                auth_type=KubeClientAuthType.TOKEN,
                url=URL("https://kubernetes.default.svc"),
                token_path="/var/run/secrets/kubernetes.io/serviceaccount/token",
                cert_authority_path=(
                    "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
                ),
            ),
            cluster_name="default",
            node_name="node",
        )

    def test_create_metrics_custom(self) -> None:
        env = {
            "NP_METRICS_API_SCHEME": "http",
            "NP_METRICS_API_HOST": "metrics",
            "NP_METRICS_API_PORT": "9500",
            "NP_CONFIG_URL": "http://dev.neu.ro",
            "NP_CONFIG_TOKEN": "token",
            "NP_CLUSTER_NAME": "default",
            "NP_NODE_NAME": "node",
            "NP_CLOUD_PROVIDER": "aws",
            "NP_REGION": "us-east-1",
            "NP_GCP_SERVICE_ACCOUNT_KEY_PATH": "sa.json",
            "NP_AZURE_PRICES_URL": "https://azure-prices",
            "NP_JOBS_NAMESPACE": "platform-jobs",
            "NP_NODE_POOL_LABEL": "node-pool",
            "NP_NODE_PREEMPTIBLE_LABEL": "preemptible",
            "NP_JOB_LABEL": "job",
            "NP_PRESET_LABEL": "preset",
            "NP_ZIPKIN_URL": "https://zipkin:9411",
            "NP_SENTRY_DSN": "https://sentry",
            "NP_SENTRY_CLUSTER_NAME": "test",
        }

        result = EnvironConfigFactory(env).create_metrics()

        assert result == MetricsConfig(
            server=ServerConfig(scheme="http", host="metrics", port=9500),
            platform_config=PlatformServiceConfig(
                url=URL("http://dev.neu.ro"), token="token"
            ),
            kube=KubeConfig(
                auth_type=KubeClientAuthType.TOKEN,
                url=URL("https://kubernetes.default.svc"),
                token_path="/var/run/secrets/kubernetes.io/serviceaccount/token",
                cert_authority_path=(
                    "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
                ),
            ),
            cluster_name="default",
            node_name="node",
            cloud_provider="aws",
            region="us-east-1",
            gcp_service_account_key_path=Path("sa.json"),
            azure_prices_url=URL("https://azure-prices"),
            jobs_namespace="platform-jobs",
            node_pool_label="node-pool",
            node_preemptible_label="preemptible",
            job_label="job",
            preset_label="preset",
            zipkin=ZipkinConfig(
                url=URL("https://zipkin:9411"), app_name="platform-metrics-exporter"
            ),
            sentry=SentryConfig(
                dsn=URL("https://sentry"),
                app_name="platform-metrics-exporter",
                cluster_name="test",
            ),
        )

    def test_create_prometheus_proxy_defaults(self) -> None:
        env = {
            "NP_CLUSTER_NAME": "default",
            "NP_AUTH_ACCESS_TOKEN_COOKIE_NAMES": "sat,dat",
            "NP_PROMETHEUS_HOST": "prometheus",
            "NP_PROMETHEUS_PORT": "9090",
            "NP_AUTH_URL": "https://dev.neu.ro",
            "NP_AUTH_TOKEN": "token",
            "NP_API_URL": "https://dev.neu.ro/api/v1",
        }

        result = EnvironConfigFactory(env).create_prometheus_proxy()

        assert result == PrometheusProxyConfig(
            cluster_name="default",
            access_token_cookie_names=["sat", "dat"],
            server=ServerConfig(),
            prometheus_server=ServerConfig(host="prometheus", port=9090),
            platform_auth=PlatformServiceConfig(
                url=URL("https://dev.neu.ro"), token="token"
            ),
            platform_api=PlatformServiceConfig(
                url=URL("https://dev.neu.ro/api/v1"), token="token"
            ),
        )

    def test_create_prometheus_proxy_custom(self) -> None:
        env = {
            "NP_CLUSTER_NAME": "default",
            "NP_AUTH_ACCESS_TOKEN_COOKIE_NAMES": "sat,dat",
            "NP_REPORTS_API_SCHEME": "https",
            "NP_REPORTS_API_HOST": "platform-reports",
            "NP_REPORTS_API_PORT": "80",
            "NP_PROMETHEUS_SCHEME": "https",
            "NP_PROMETHEUS_HOST": "prometheus",
            "NP_PROMETHEUS_PORT": "9090",
            "NP_AUTH_URL": "https://dev.neu.ro",
            "NP_AUTH_TOKEN": "token",
            "NP_API_URL": "https://dev.neu.ro/api/v1",
            "NP_ZIPKIN_URL": "https://zipkin:9411",
            "NP_SENTRY_DSN": "https://sentry",
            "NP_SENTRY_CLUSTER_NAME": "test",
        }

        result = EnvironConfigFactory(env).create_prometheus_proxy()

        assert result == PrometheusProxyConfig(
            cluster_name="default",
            access_token_cookie_names=["sat", "dat"],
            server=ServerConfig(scheme="https", host="platform-reports", port=80),
            prometheus_server=ServerConfig(
                scheme="https", host="prometheus", port=9090
            ),
            platform_auth=PlatformServiceConfig(
                url=URL("https://dev.neu.ro"), token="token"
            ),
            platform_api=PlatformServiceConfig(
                url=URL("https://dev.neu.ro/api/v1"), token="token"
            ),
            zipkin=ZipkinConfig(
                url=URL("https://zipkin:9411"), app_name="platform-prometheus-proxy"
            ),
            sentry=SentryConfig(
                dsn=URL("https://sentry"),
                app_name="platform-prometheus-proxy",
                cluster_name="test",
            ),
        )

    def test_create_grafana_proxy_defaults(self) -> None:
        env = {
            "NP_CLUSTER_NAME": "default",
            "NP_AUTH_ACCESS_TOKEN_COOKIE_NAMES": "sat,dat",
            "NP_GRAFANA_HOST": "grafana",
            "NP_GRAFANA_PORT": "3000",
            "NP_AUTH_URL": "https://dev.neu.ro",
            "NP_AUTH_TOKEN": "token",
            "NP_API_URL": "https://dev.neu.ro/api/v1",
        }

        result = EnvironConfigFactory(env).create_grafana_proxy()

        assert result == GrafanaProxyConfig(
            cluster_name="default",
            access_token_cookie_names=["sat", "dat"],
            server=ServerConfig(),
            grafana_server=ServerConfig(host="grafana", port=3000),
            platform_auth=PlatformServiceConfig(
                url=URL("https://dev.neu.ro"), token="token"
            ),
            platform_api=PlatformServiceConfig(
                url=URL("https://dev.neu.ro/api/v1"), token="token"
            ),
        )

    def test_create_grafana_proxy_custom(self) -> None:
        env = {
            "NP_CLUSTER_NAME": "default",
            "NP_AUTH_ACCESS_TOKEN_COOKIE_NAMES": "sat,dat",
            "NP_REPORTS_API_SCHEME": "https",
            "NP_REPORTS_API_HOST": "platform-reports",
            "NP_REPORTS_API_PORT": "80",
            "NP_GRAFANA_SCHEME": "https",
            "NP_GRAFANA_HOST": "grafana",
            "NP_GRAFANA_PORT": "3000",
            "NP_AUTH_URL": "https://dev.neu.ro",
            "NP_AUTH_TOKEN": "token",
            "NP_API_URL": "https://dev.neu.ro/api/v1",
            "NP_ZIPKIN_URL": "https://zipkin:9411",
            "NP_SENTRY_DSN": "https://sentry",
            "NP_SENTRY_CLUSTER_NAME": "test",
        }

        result = EnvironConfigFactory(env).create_grafana_proxy()

        assert result == GrafanaProxyConfig(
            cluster_name="default",
            access_token_cookie_names=["sat", "dat"],
            server=ServerConfig(scheme="https", host="platform-reports", port=80),
            grafana_server=ServerConfig(scheme="https", host="grafana", port=3000),
            platform_auth=PlatformServiceConfig(
                url=URL("https://dev.neu.ro"), token="token"
            ),
            platform_api=PlatformServiceConfig(
                url=URL("https://dev.neu.ro/api/v1"), token="token"
            ),
            zipkin=ZipkinConfig(
                url=URL("https://zipkin:9411"), app_name="platform-grafana-proxy"
            ),
            sentry=SentryConfig(
                dsn=URL("https://sentry"),
                app_name="platform-grafana-proxy",
                cluster_name="test",
            ),
        )

    def test_create_zipkin_none(self) -> None:
        result = EnvironConfigFactory({}).create_zipkin(default_app_name="app")

        assert result is None

    def test_create_zipkin_default(self) -> None:
        env = {"NP_ZIPKIN_URL": "https://zipkin:9411"}
        result = EnvironConfigFactory(env).create_zipkin(default_app_name="app")

        assert result == ZipkinConfig(url=URL("https://zipkin:9411"), app_name="app")

    def test_create_zipkin_custom(self) -> None:
        env = {
            "NP_ZIPKIN_URL": "https://zipkin:9411",
            "NP_ZIPKIN_APP_NAME": "api",
            "NP_ZIPKIN_SAMPLE_RATE": "1",
        }
        result = EnvironConfigFactory(env).create_zipkin(default_app_name="app")

        assert result == ZipkinConfig(
            url=URL("https://zipkin:9411"), app_name="api", sample_rate=1
        )

    def test_create_sentry_none(self) -> None:
        result = EnvironConfigFactory({}).create_sentry(default_app_name="app")

        assert result is None

    def test_create_sentry_default(self) -> None:
        env = {
            "NP_SENTRY_DSN": "https://sentry",
            "NP_SENTRY_CLUSTER_NAME": "test",
        }
        result = EnvironConfigFactory(env).create_sentry(default_app_name="app")

        assert result == SentryConfig(
            dsn=URL("https://sentry"), app_name="app", cluster_name="test"
        )

    def test_create_sentry_custom(self) -> None:
        env = {
            "NP_SENTRY_DSN": "https://sentry",
            "NP_SENTRY_APP_NAME": "api",
            "NP_SENTRY_CLUSTER_NAME": "test",
            "NP_SENTRY_SAMPLE_RATE": "1",
        }
        result = EnvironConfigFactory(env).create_sentry(default_app_name="app")

        assert result == SentryConfig(
            dsn=URL("https://sentry"),
            app_name="api",
            cluster_name="test",
            sample_rate=1,
        )

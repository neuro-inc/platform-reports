from __future__ import annotations

import re
import time
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from datetime import datetime, timedelta
from decimal import Decimal
from unittest import mock

import aiohttp
import pytest
from aiohttp.web import HTTPForbidden, HTTPOk, HTTPUnauthorized, HTTPUnprocessableEntity
from neuro_auth_client import Permission
from yarl import URL

from platform_reports.api import create_metrics_api_app
from platform_reports.config import MetricsApiConfig, MetricsExporterConfig
from platform_reports.kube_client import Node
from platform_reports.metrics_service import CreditsUsage, MetricsService
from platform_reports.schema import CategoryName

from .conftest import create_local_app_server
from .conftest_kube import KubeClient, KubePodFactory
from .conftest_platform_auth import User, UserFactory


class TestMetricsExporterApi:
    async def test_ping(
        self, client: aiohttp.ClientSession, metrics_exporter_server: URL
    ) -> None:
        async with client.get(metrics_exporter_server / "ping") as response:
            assert response.status == HTTPOk.status_code

    async def test_node_price_metrics(
        self,
        client: aiohttp.ClientSession,
        metrics_exporter_server: URL,
        kube_node: Node,
    ) -> None:
        async with client.get(metrics_exporter_server / "metrics") as response:
            text = await response.text()
            assert response.status == HTTPOk.status_code, text
            assert (
                f"""\
# HELP kube_node_price_total The total price of the node.
# TYPE kube_node_price_total counter
kube_node_price_total{{node="{kube_node.metadata.name}",currency="USD"}} 0.00"""
                in text
            )

    async def test_pod_credits_metrics(
        self,
        client: aiohttp.ClientSession,
        metrics_exporter_server_factory: Callable[
            [MetricsExporterConfig], AbstractAsyncContextManager[URL]
        ],
        metrics_exporter_config: MetricsExporterConfig,
        kube_client: KubeClient,
        kube_pod_factory: KubePodFactory,
    ) -> None:
        pod = await kube_pod_factory(
            "default",
            {
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {
                    "generateName": "test-",
                    "labels": {"platform.apolo.us/preset": "test-preset"},
                },
                "spec": {
                    "restartPolicy": "Never",
                    "containers": [
                        {
                            "name": "ubuntu",
                            "image": "ubuntu:20.04",
                            "command": ["bash"],
                            "args": ["-c", "sleep 60"],
                        }
                    ],
                },
            },
        )
        await kube_client.wait_pod_is_running(
            pod["metadata"]["namespace"], pod["metadata"]["name"]
        )

        async with metrics_exporter_server_factory(metrics_exporter_config) as server:
            async with client.get(server / "metrics") as response:
                text = await response.text()
                assert response.status == HTTPOk.status_code, text
                assert re.search(
                    r"""
\# HELP kube_pod_credits_total The total credits consumed by the pod\.
\# TYPE kube_pod_credits_total counter
(kube_pod_credits_total\{pod=".+"\} [0-9]+(\.[0-9]+)?\s*)+""",
                    text,
                ), text

    async def test_node_power_metrics(
        self,
        client: aiohttp.ClientSession,
        metrics_exporter_server_factory: Callable[
            [MetricsExporterConfig], AbstractAsyncContextManager[URL]
        ],
        metrics_exporter_config: MetricsExporterConfig,
        kube_node: Node,
    ) -> None:
        async with metrics_exporter_server_factory(metrics_exporter_config) as server:
            async with client.get(server / "metrics") as response:
                text = await response.text()
                assert response.status == HTTPOk.status_code, text
                assert re.search(
                    rf"""# HELP cpu_min_watts The CPU power consumption while IDLEing in watts
# TYPE cpu_min_watts gauge
cpu_min_watts{{node="{kube_node.metadata.name}"}} \d+\.?\d*
# HELP cpu_max_watts The CPU power consumption when fully utilized in watts
# TYPE cpu_max_watts gauge
cpu_max_watts{{node="{kube_node.metadata.name}"}} \d+\.?\d*
# HELP co2_grams_eq_per_kwh Estimated CO2 emission for energy generation in region where the node is running
# TYPE co2_grams_eq_per_kwh gauge
co2_grams_eq_per_kwh{{node="{kube_node.metadata.name}"}} \d+\.?\d*
# HELP price_per_kwh Energy price per kwh in region where the node is running
# TYPE price_per_kwh gauge
price_per_kwh{{node="{kube_node.metadata.name}"}} \d+\.?\d*""",  # noqa: E501
                    text,
                ), text


class TestPrometheusProxy:
    async def test_ping(
        self, client: aiohttp.ClientSession, prometheus_proxy_server: URL
    ) -> None:
        async with client.get(prometheus_proxy_server / "api/v1/ping") as response:
            assert response.status == HTTPOk.status_code

    async def test_query(
        self,
        client: aiohttp.ClientSession,
        cluster_admin_token: str,
        prometheus_proxy_server: URL,
    ) -> None:
        async with client.get(
            (prometheus_proxy_server / "api/v1/query").with_query(
                query='node_cpu_seconds_total{job="node-exporter"}'
            ),
            cookies={"dat": cluster_admin_token},
        ) as response:
            assert response.status == HTTPOk.status_code

    async def test_query_forbidden(
        self,
        client: aiohttp.ClientSession,
        regular_user_token: str,
        prometheus_proxy_server: URL,
    ) -> None:
        async with client.get(
            (prometheus_proxy_server / "api/v1/query").with_query(
                query='node_cpu_seconds_total{job="node-exporter"}'
            ),
            cookies={"dat": regular_user_token},
        ) as response:
            assert response.status == HTTPForbidden.status_code

    async def test_query_range(
        self,
        client: aiohttp.ClientSession,
        cluster_admin_token: str,
        prometheus_proxy_server: URL,
    ) -> None:
        now = int(time.time())
        async with client.get(
            (prometheus_proxy_server / "api/v1/query_range").with_query(
                query='node_cpu_seconds_total{job="node-exporter"}',
                step=5,
                start=now - 60,
                end=now,
            ),
            cookies={"dat": cluster_admin_token},
        ) as response:
            assert response.status == HTTPOk.status_code

    async def test_query_forbidden_range(
        self,
        client: aiohttp.ClientSession,
        regular_user_token: str,
        prometheus_proxy_server: URL,
    ) -> None:
        now = int(time.time())
        async with client.get(
            (prometheus_proxy_server / "api/v1/query_range").with_query(
                query='node_cpu_seconds_total{job="node-exporter"}',
                step=5,
                start=now - 60,
                end=now,
            ),
            cookies={"dat": regular_user_token},
        ) as response:
            assert response.status == HTTPForbidden.status_code

    async def test_series(
        self,
        client: aiohttp.ClientSession,
        cluster_admin_token: str,
        prometheus_proxy_server: URL,
    ) -> None:
        async with client.get(
            (prometheus_proxy_server / "api/v1/series").with_query(
                [("match[]", 'node_cpu_seconds_total{job="node-exporter"}')]
            ),
            cookies={"dat": cluster_admin_token},
        ) as response:
            assert response.status == HTTPOk.status_code

    async def test_series_forbidden(
        self,
        client: aiohttp.ClientSession,
        regular_user_token: str,
        prometheus_proxy_server: URL,
    ) -> None:
        async with client.get(
            (prometheus_proxy_server / "api/v1/series").with_query(
                [("match[]", 'node_cpu_seconds_total{job="node-exporter"}')]
            ),
            cookies={"dat": regular_user_token},
        ) as response:
            assert response.status == HTTPForbidden.status_code

    async def test_label_values(
        self,
        client: aiohttp.ClientSession,
        cluster_admin_token: str,
        prometheus_proxy_server: URL,
    ) -> None:
        async with client.get(
            prometheus_proxy_server / "api/v1/label/job/values",
            cookies={"dat": cluster_admin_token},
        ) as response:
            assert response.status == HTTPOk.status_code

    async def test_label_values_forbidden(
        self,
        client: aiohttp.ClientSession,
        regular_user_token: str,
        prometheus_proxy_server: URL,
    ) -> None:
        async with client.get(
            prometheus_proxy_server / "api/v1/label/job/values",
            cookies={"dat": regular_user_token},
        ) as response:
            assert response.status == HTTPForbidden.status_code

    async def test_cluster_label_present(
        self,
        client: aiohttp.ClientSession,
        cluster_admin_token: str,
        prometheus_proxy_server: URL,
    ) -> None:
        async with client.get(
            (prometheus_proxy_server / "api/v1/query").with_query(
                query='node_cpu_seconds_total{job="node-exporter"}'
            ),
            cookies={"dat": cluster_admin_token},
        ) as response:
            assert response.status == HTTPOk.status_code
            metrics = await response.json()
            assert all("cluster" in r["metric"] for r in metrics["data"]["result"])
            assert all(
                r["metric"]["cluster"] == "dev" for r in metrics["data"]["result"]
            )


class TestGrafanaProxy:
    async def test_ping(
        self, client: aiohttp.ClientSession, grafana_proxy_server: URL
    ) -> None:
        async with client.get(grafana_proxy_server / "ping") as response:
            assert response.status == HTTPOk.status_code

    async def test_ping_includes_version(
        self, client: aiohttp.ClientSession, grafana_proxy_server: URL
    ) -> None:
        async with client.get(grafana_proxy_server / "ping") as response:
            assert response.status == HTTPOk.status_code
            assert "platform-reports" in response.headers["X-Service-Version"]

    async def test_main(
        self,
        client: aiohttp.ClientSession,
        cluster_admin_token: str,
        grafana_proxy_server: URL,
    ) -> None:
        async with client.get(
            grafana_proxy_server, cookies={"dat": cluster_admin_token}
        ) as response:
            assert response.status == HTTPOk.status_code

    async def test_main_forbidden(
        self,
        client: aiohttp.ClientSession,
        other_cluster_user_token: str,
        grafana_proxy_server: URL,
    ) -> None:
        async with client.get(
            grafana_proxy_server, cookies={"dat": other_cluster_user_token}
        ) as response:
            assert response.status == HTTPForbidden.status_code

    async def test_dashboard(
        self,
        client: aiohttp.ClientSession,
        cluster_admin_token: str,
        grafana_proxy_server: URL,
    ) -> None:
        async with client.get(
            grafana_proxy_server / "api/dashboards/uid/nodes",
            cookies={"dat": cluster_admin_token},
        ) as response:
            assert response.status == HTTPOk.status_code

    async def test_dashboard_forbidden(
        self,
        client: aiohttp.ClientSession,
        regular_user_token: str,
        grafana_proxy_server: URL,
    ) -> None:
        async with client.get(
            grafana_proxy_server / "api/dashboards/uid/nodes",
            cookies={"dat": regular_user_token},
        ) as response:
            assert response.status == HTTPForbidden.status_code


class TestMetricsApi:
    @pytest.fixture
    async def user(self, user_factory: UserFactory) -> User:
        return await user_factory(
            str(uuid.uuid4()), [Permission("cluster://default", "read")]
        )

    async def test_ping(
        self, client: aiohttp.ClientSession, metrics_api_server: URL
    ) -> None:
        async with client.get(metrics_api_server / "ping") as response:
            assert response.status == HTTPOk.status_code, await response.text()

    async def test_post_credits_usage__unauthorized(
        self, client: aiohttp.ClientSession, metrics_api_server: URL
    ) -> None:
        async with client.post(
            metrics_api_server / "api/v1/metrics/credits/usage",
            json={
                "start_date": (datetime.now() - timedelta(hours=1)).isoformat(),
                "end_date": datetime.now().isoformat(),
            },
        ) as response:
            assert (
                response.status == HTTPUnauthorized.status_code
            ), await response.text()

    async def test_post_credits_usage__forbidden(
        self,
        client: aiohttp.ClientSession,
        metrics_api_server: URL,
        user_factory: UserFactory,
    ) -> None:
        user = await user_factory(str(uuid.uuid4()), [])
        async with client.post(
            metrics_api_server / "api/v1/metrics/credits/usage",
            headers={"Authorization": f"Bearer {user.token}"},
            json={
                "start_date": (datetime.now() - timedelta(hours=1)).isoformat(),
                "end_date": datetime.now().isoformat(),
            },
        ) as response:
            assert response.status == HTTPForbidden.status_code, await response.text()

    async def test_post_credits_usage__bad_request(
        self, client: aiohttp.ClientSession, user: User, metrics_api_server: URL
    ) -> None:
        async with client.post(
            metrics_api_server / "api/v1/metrics/credits/usage",
            headers={"Authorization": f"Bearer {user.token}"},
            json={},
        ) as response:
            assert (
                response.status == HTTPUnprocessableEntity.status_code
            ), await response.text()

    async def test_post_credits_usage(
        self, client: aiohttp.ClientSession, user: User, metrics_api_server: URL
    ) -> None:
        async with client.post(
            metrics_api_server / "api/v1/metrics/credits/usage",
            headers={"Authorization": f"Bearer {user.token}"},
            json={
                "start_date": (datetime.now() - timedelta(hours=1)).isoformat(),
                "end_date": datetime.now().isoformat(),
            },
        ) as response:
            assert response.status == HTTPOk.status_code, await response.text()

    async def test_post_credits_usage__with_org_and_project(
        self, client: aiohttp.ClientSession, user: User, metrics_api_server: URL
    ) -> None:
        async with client.post(
            metrics_api_server / "api/v1/metrics/credits/usage",
            headers={"Authorization": f"Bearer {user.token}"},
            json={
                "org_name": "test-org",
                "project_name": "test-project",
                "start_date": (datetime.now() - timedelta(hours=1)).isoformat(),
                "end_date": datetime.now().isoformat(),
            },
        ) as response:
            assert response.status == HTTPOk.status_code, await response.text()

    async def test_post_credits_usage__mocked(
        self,
        client: aiohttp.ClientSession,
        user: User,
        metrics_api_config: MetricsApiConfig,
        exit_stack: AsyncExitStack,
    ) -> None:
        mocked_service_cls = exit_stack.enter_context(
            mock.patch("platform_reports.api.MetricsService", spec=MetricsService)
        )
        mocked_service = mocked_service_cls.return_value
        mocked_service.get_credits_usage.return_value = [
            CreditsUsage(
                category_name=CategoryName.JOBS,
                project_name="test-project",
                user_name="test-user",
                resource_id="test-job",
                credits=Decimal(1),
            )
        ]

        server_address = await exit_stack.enter_async_context(
            create_local_app_server(
                app=create_metrics_api_app(metrics_api_config),
                port=metrics_api_config.server.port,
            )
        )

        async with client.post(
            server_address.http_url / "api/v1/metrics/credits/usage",
            headers={"Authorization": f"Bearer {user.token}"},
            json={
                "start_date": (datetime.now() - timedelta(hours=1)).isoformat(),
                "end_date": datetime.now().isoformat(),
            },
        ) as response:
            assert response.status == HTTPOk.status_code, await response.text()
            assert await response.json() == [
                {
                    "category_name": "jobs",
                    "org_name": None,
                    "project_name": "test-project",
                    "user_name": "test-user",
                    "resource_id": "test-job",
                    "credits": "1",
                }
            ]

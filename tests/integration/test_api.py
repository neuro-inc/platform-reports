from __future__ import annotations

import re
import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import replace

import aiohttp
from aiohttp.web import HTTPForbidden, HTTPOk
from yarl import URL

from platform_reports.config import MetricsConfig
from platform_reports.kube_client import Node


class TestMetrics:
    async def test_ping(
        self, client: aiohttp.ClientSession, metrics_server: URL
    ) -> None:
        async with client.get(metrics_server / "ping") as response:
            assert response.status == HTTPOk.status_code

    async def test_node_metrics(
        self, client: aiohttp.ClientSession, metrics_server: URL, kube_node: Node
    ) -> None:
        async with client.get(metrics_server / "metrics") as response:
            text = await response.text()
            assert response.status == HTTPOk.status_code, text
            assert (
                text
                == f"""\
# HELP kube_node_price_total The total price of the node.
# TYPE kube_node_price_total counter
kube_node_price_total{{node="{kube_node.metadata.name}",currency="USD"}} 0.0"""
            )

    async def test_node_and_pod_metrics(
        self,
        client: aiohttp.ClientSession,
        metrics_server_factory: Callable[
            [MetricsConfig], AbstractAsyncContextManager[URL]
        ],
        metrics_config: MetricsConfig,
        kube_node: Node,
    ) -> None:
        metrics_config = replace(metrics_config, job_label="")
        async with metrics_server_factory(metrics_config) as server:
            async with client.get(server / "metrics") as response:
                text = await response.text()
                assert response.status == HTTPOk.status_code, text
                assert re.search(
                    rf"""# HELP kube_node_price_total The total price of the node\.
\# TYPE kube_node_price_total counter
kube_node_price_total{{node="{kube_node.metadata.name}",currency="USD"}} 0(\.0+)?

\# HELP kube_pod_credits_total The total credits of the pod\.
\# TYPE kube_pod_credits_total counter
(kube_pod_credits_total{{pod=".+"}} 0(\.0+)?\s*)+""",
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

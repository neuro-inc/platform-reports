import re
import time
from dataclasses import replace
from typing import AsyncContextManager, Callable

import aiohttp
import pytest
from aiohttp.web import HTTPForbidden, HTTPOk
from yarl import URL

from platform_reports.config import MetricsConfig


class TestMetrics:
    @pytest.mark.asyncio
    async def test_ping(
        self, client: aiohttp.ClientSession, metrics_server: URL
    ) -> None:
        async with client.get(metrics_server / "ping") as response:
            assert response.status == HTTPOk.status_code

    @pytest.mark.asyncio
    async def test_node_metrics(
        self, client: aiohttp.ClientSession, metrics_server: URL
    ) -> None:
        async with client.get(metrics_server / "metrics") as response:
            text = await response.text()
            assert response.status == HTTPOk.status_code, text
            assert (
                text
                == """\
# HELP kube_node_price_per_hour The price of the node per hour.
# TYPE kube_node_price_per_hour gauge
kube_node_price_per_hour{node="minikube",currency=""} 0.0"""
            )

    @pytest.mark.asyncio
    async def test_node_and_pod_metrics(
        self,
        client: aiohttp.ClientSession,
        metrics_server_factory: Callable[[MetricsConfig], AsyncContextManager[URL]],
        metrics_config: MetricsConfig,
    ) -> None:
        metrics_config = replace(metrics_config, job_label="")
        async with metrics_server_factory(metrics_config) as server:
            async with client.get(server / "metrics") as response:
                text = await response.text()
                assert response.status == HTTPOk.status_code, text
                assert re.search(
                    r"""\# HELP kube_node_price_per_hour The price of the node per hour\.
\# TYPE kube_node_price_per_hour gauge
kube_node_price_per_hour\{node="minikube",currency=""\} 0\.0

\# HELP kube_pod_price_per_hour The price of the pod per hour.
\# TYPE kube_pod_price_per_hour gauge
(kube_pod_price_per_hour\{pod=".+",currency=".*"\} 0\.0\s*)+""",
                    text,
                ), text


class TestPrometheusProxy:
    @pytest.mark.asyncio
    async def test_ping(
        self, client: aiohttp.ClientSession, prometheus_proxy_server: URL
    ) -> None:
        async with client.get(prometheus_proxy_server / "api/v1/ping") as response:
            assert response.status == HTTPOk.status_code

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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
    @pytest.mark.asyncio
    async def test_ping(
        self, client: aiohttp.ClientSession, grafana_proxy_server: URL
    ) -> None:
        async with client.get(grafana_proxy_server / "ping") as response:
            assert response.status == HTTPOk.status_code

    @pytest.mark.asyncio
    async def test_main(
        self,
        client: aiohttp.ClientSession,
        cluster_admin_token: str,
        grafana_proxy_server: URL,
    ) -> None:
        async with client.get(
            grafana_proxy_server, cookies={"dat": cluster_admin_token},
        ) as response:
            assert response.status == HTTPOk.status_code

    @pytest.mark.asyncio
    async def test_main_forbidden(
        self,
        client: aiohttp.ClientSession,
        other_cluster_user_token: str,
        grafana_proxy_server: URL,
    ) -> None:
        async with client.get(
            grafana_proxy_server, cookies={"dat": other_cluster_user_token},
        ) as response:
            assert response.status == HTTPForbidden.status_code

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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

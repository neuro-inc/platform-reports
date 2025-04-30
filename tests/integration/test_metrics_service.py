from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import aiohttp
import aiohttp.web
import pytest
from aiohttp.test_utils import TestClient
from neuro_config_client import Cluster, ClusterStatus, StorageConfig, VolumeConfig
from yarl import URL

from platform_reports.cluster import ClusterHolder
from platform_reports.metrics_service import (
    CreditsUsage,
    GetCreditsUsageRequest,
    MetricsService,
)
from platform_reports.prometheus_client import PrometheusClient
from platform_reports.schema import CategoryName


class PrometheusHandler:
    def __init__(self) -> None:
        self.post_query_range = self._post_query_range

    async def do_post_query_range(
        self, request: aiohttp.web.Request
    ) -> aiohttp.web.Response:
        return await self.post_query_range(request)

    async def _post_query_range(
        self, request: aiohttp.web.Request
    ) -> aiohttp.web.Response:
        return aiohttp.web.json_response({})


@pytest.fixture
def prometheus_handler() -> PrometheusHandler:
    return PrometheusHandler()


@pytest.fixture
def prometheus_app(prometheus_handler: PrometheusHandler) -> aiohttp.web.Application:
    app = aiohttp.web.Application()
    app.router.add_post("/api/v1/query_range", prometheus_handler.do_post_query_range)
    return app


@pytest.fixture
async def prometheus_test_client(
    aiohttp_client: Callable[[aiohttp.web.Application], Awaitable[TestClient]],
    prometheus_app: aiohttp.web.Application,
) -> TestClient:
    return await aiohttp_client(prometheus_app)


@pytest.fixture
def prometheus_client(
    prometheus_test_client: TestClient,
) -> PrometheusClient:
    return PrometheusClient(
        client=prometheus_test_client.session,
        prometheus_url=prometheus_test_client.make_url(""),
    )


class _TestClusterHolder(ClusterHolder):
    def __init__(self, cluster: Cluster) -> None:
        self._cluster = cluster

    @property
    def cluster(self) -> Cluster:
        return self._cluster


@pytest.fixture
def cluster() -> Cluster:
    return Cluster(
        name="default",
        status=ClusterStatus.DEPLOYED,
        created_at=datetime.now(),
        storage=StorageConfig(
            url=URL("http://platform-storage.platform"),
            volumes=[
                VolumeConfig(name="default", credits_per_hour_per_gb=Decimal(100))
            ],
        ),
    )


class TestMetricsService:
    @pytest.fixture
    def metrics_service(
        self, prometheus_client: PrometheusClient, cluster: Cluster
    ) -> MetricsService:
        return MetricsService(
            prometheus_client=prometheus_client,
            cluster_holder=_TestClusterHolder(cluster),
        )

    async def test_get_credits_usage(
        self, metrics_service: MetricsService, prometheus_handler: PrometheusHandler
    ) -> None:
        consumption_request = GetCreditsUsageRequest(
            start_date=datetime.now(UTC) - timedelta(hours=1),
            end_date=datetime.now(UTC),
        )

        async def post_query_range(
            request: aiohttp.web.Request,
        ) -> aiohttp.web.Response:
            request_text = await request.text()

            if "kube_pod_credits_total" in request_text:
                return aiohttp.web.json_response(
                    {
                        "status": "success",
                        "data": {
                            "resultType": "matrix",
                            "result": [
                                {
                                    "metric": {},
                                    "values": [[1719075883, "1"], [1719075898, "2"]],
                                },
                                {
                                    "metric": {
                                        "label_platform_neuromation_io_org": "test-org",
                                        "label_platform_neuromation_io_project": "test-project",  # noqa: E501
                                        "label_platform_neuromation_io_user": "test-user",  # noqa: E501
                                        "label_platform_neuromation_io_job": "test-job",
                                    },
                                    "values": [[1719075883, "1"], [1719075898, "2"]],
                                },
                            ],
                        },
                    }
                )

            if "storage_used_bytes" in request_text:
                return aiohttp.web.json_response(
                    {
                        "status": "success",
                        "data": {
                            "resultType": "matrix",
                            "result": [
                                {
                                    "metric": {
                                        "org_name": "test-org",
                                        "project_name": "test-project",
                                    },
                                    "values": [
                                        [1719075883, str(1000**3)],
                                        [1719079483, str(1000**3)],
                                    ],
                                },
                            ],
                        },
                    }
                )

            return aiohttp.web.json_response(
                {"status": "success", "data": {"resultType": "matrix", "result": []}}
            )

        prometheus_handler.post_query_range = post_query_range

        consumptions = await metrics_service.get_credits_usage(consumption_request)

        assert consumptions == [
            CreditsUsage(
                category_name=CategoryName.JOBS,
                project_name="test-project",
                user_name="test-user",
                resource_id="test-job",
                credits=Decimal("1"),
                org_name="test-org",
            ),
            CreditsUsage(
                category_name=CategoryName.STORAGE,
                project_name="test-project",
                resource_id="default",
                credits=Decimal("100"),
                org_name="test-org",
            ),
        ]

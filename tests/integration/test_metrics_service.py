from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import aiohttp
import aiohttp.web
import pytest
from aiohttp.test_utils import TestClient

from platform_reports.metrics_service import (
    CreditsConsumption,
    CreditsConsumptionRequest,
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


@pytest.fixture()
def prometheus_handler() -> PrometheusHandler:
    return PrometheusHandler()


@pytest.fixture()
def prometheus_app(prometheus_handler: PrometheusHandler) -> aiohttp.web.Application:
    app = aiohttp.web.Application()
    app.router.add_post("/api/v1/query_range", prometheus_handler.do_post_query_range)
    return app


@pytest.fixture()
async def prometheus_test_client(
    aiohttp_client: Callable[[aiohttp.web.Application], Awaitable[TestClient]],
    prometheus_app: aiohttp.web.Application,
) -> TestClient:
    return await aiohttp_client(prometheus_app)


@pytest.fixture()
def prometheus_client(
    prometheus_test_client: TestClient,
) -> PrometheusClient:
    return PrometheusClient(
        client=prometheus_test_client.session,
        prometheus_url=prometheus_test_client.make_url(""),
    )


class TestMetricsService:
    @pytest.fixture()
    def metrics_service(self, prometheus_client: PrometheusClient) -> MetricsService:
        return MetricsService(prometheus_client=prometheus_client)

    async def test_get_credits_consumption(
        self, metrics_service: MetricsService, prometheus_handler: PrometheusHandler
    ) -> None:
        consumption_request = CreditsConsumptionRequest(
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
                                        "label_platform_neuromation_io_job": "test-job",
                                    },
                                    "values": [[1719075883, "1"], [1719075898, "2"]],
                                },
                            ],
                        },
                    }
                )

            return aiohttp.web.json_response(
                {"status": "success", "data": {"resultType": "matrix", "result": []}}
            )

        prometheus_handler.post_query_range = post_query_range

        consumptions = await metrics_service.get_credits_consumption(
            consumption_request
        )

        assert consumptions == [
            CreditsConsumption(
                category_name=CategoryName.JOBS,
                project_name="test-project",
                resource_id="test-job",
                credits=Decimal("1"),
                org_name="test-org",
            )
        ]

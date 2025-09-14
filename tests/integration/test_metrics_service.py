from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import aiohttp
import aiohttp.web
import pytest
from aiohttp.test_utils import TestClient
from aiohttp.web import Application, Request
from neuro_config_client import (
    ACMEEnvironment,
    AppsConfig,
    BucketsConfig,
    Cluster,
    DisksConfig,
    DNSConfig,
    EnergyConfig,
    IngressConfig,
    MetricsConfig,
    MonitoringConfig,
    OrchestratorConfig,
    RegistryConfig,
    SecretsConfig,
    StorageConfig,
    VolumeConfig,
)
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
    aiohttp_client: Callable[
        [aiohttp.web.Application], Awaitable[TestClient[Request, Application]]
    ],
    prometheus_app: aiohttp.web.Application,
) -> TestClient[Request, Application]:
    return await aiohttp_client(prometheus_app)


@pytest.fixture
def prometheus_client(
    prometheus_test_client: TestClient[Request, Application],
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
        created_at=datetime.now(),
        orchestrator=OrchestratorConfig(
            job_hostname_template="",
            job_fallback_hostname="",
            job_schedule_timeout_s=30,
            job_schedule_scale_up_timeout_s=30,
        ),
        storage=StorageConfig(
            url=URL("https://default.org.apolo.us"),
            volumes=[
                VolumeConfig(name="default", credits_per_hour_per_gb=Decimal(100))
            ],
        ),
        registry=RegistryConfig(url=URL("https://default.org.apolo.us")),
        buckets=BucketsConfig(url=URL("https://default.org.apolo.us")),
        disks=DisksConfig(
            url=URL("https://default.org.apolo.us"),
            storage_limit_per_user=10240 * 2**30,
        ),
        monitoring=MonitoringConfig(url=URL("https://default.org.apolo.us")),
        dns=DNSConfig(name="default.org.apolo.us"),
        ingress=IngressConfig(acme_environment=ACMEEnvironment.PRODUCTION),
        secrets=SecretsConfig(url=URL("https://default.org.apolo.us")),
        metrics=MetricsConfig(url=URL("https://default.org.apolo.us")),
        apps=AppsConfig(
            apps_hostname_templates=["{app_name}.apps.default.org.apolo.us"],
            app_proxy_url=URL("https://proxy.apps.default.org.apolo.us"),
        ),
        energy=EnergyConfig(),
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
                                        "label_platform_apolo_us_org": "test-org",
                                        "label_platform_apolo_us_project": "test-project",  # noqa: E501
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

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Sequence
from contextlib import (
    AbstractAsyncContextManager,
    AbstractContextManager,
    asynccontextmanager,
    contextmanager,
    suppress,
)
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest import mock
from zoneinfo import ZoneInfo

import aiohttp
import pytest
from aiobotocore.client import AioBaseClient
from aiohttp import web
from neuro_config_client import (
    Cluster,
    ClusterStatus,
    EnergyConfig,
    EnergySchedule,
    EnergySchedulePeriod,
    NodePool,
    OnPremCloudProvider,
    OrchestratorConfig,
    ResourcePoolType,
    ResourcePreset,
)
from yarl import URL

from platform_reports.cluster import ClusterHolder
from platform_reports.kube_client import (
    UTC,
    ContainerStatus,
    KubeClient,
    Metadata,
    Pod,
    PodPhase,
    PodStatus,
)
from platform_reports.metrics import (
    AWSNodePriceCollector,
    AzureNodePriceCollector,
    Collector,
    ConfigPriceCollector,
    GCPNodePriceCollector,
    NodeEnergyConsumptionCollector,
    PodCreditsCollector,
    Price,
)


class _TestClusterHolder(ClusterHolder):
    def __init__(self, cluster: Cluster) -> None:
        self._cluster = cluster

    @property
    def cluster(self) -> Cluster:
        return self._cluster


@pytest.fixture
def cluster_holder(cluster: Cluster) -> ClusterHolder:
    return _TestClusterHolder(cluster)


class TestCollector:
    @pytest.fixture
    def collector(self) -> Collector[Price]:
        return Collector(Price(), interval_s=0.1)

    @pytest.fixture
    def price_factory(self, collector: Collector[Price]) -> Iterator[mock.Mock]:
        price = Price(currency="USD", value=Decimal(1))
        with mock.patch.object(
            collector, "get_latest_value", return_value=price
        ) as mock_method:
            yield mock_method

    async def test_update(
        self, collector: Collector[Price], price_factory: mock.Mock
    ) -> None:
        factory = await collector.start()
        task: asyncio.Task[None] = asyncio.create_task(factory)  # type: ignore

        await asyncio.sleep(0.3)

        assert collector.current_value == Price(currency="USD", value=Decimal(1))
        assert price_factory.call_count >= 3

        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


class TestConfigPriceCollector:
    @pytest.fixture
    def cluster(self) -> Cluster:
        return Cluster(
            name="default",
            status=ClusterStatus.DEPLOYED,
            created_at=datetime.now(),
            orchestrator=OrchestratorConfig(
                job_hostname_template="",
                job_internal_hostname_template="",
                job_fallback_hostname="",
                job_schedule_timeout_s=30,
                job_schedule_scale_up_timeout_s=30,
                resource_pool_types=[
                    ResourcePoolType(
                        name="node-pool",
                        cpu=8,
                        memory=52 * 1024**3,
                        price=Decimal("0.9"),
                        currency="USD",
                    ),
                ],
            ),
        )

    @pytest.fixture
    async def collector_factory(
        self, cluster_holder: ClusterHolder
    ) -> Callable[..., AbstractAsyncContextManager[ConfigPriceCollector]]:
        @asynccontextmanager
        async def create(node_pool_name: str) -> AsyncIterator[ConfigPriceCollector]:
            async with ConfigPriceCollector(
                cluster_holder=cluster_holder,
                node_pool_name=node_pool_name,
                node_created_at=datetime.now(timezone.utc) - timedelta(hours=10),
            ) as collector:
                assert isinstance(collector, ConfigPriceCollector)
                yield collector

        return create

    async def test_get_latest_value(
        self,
        collector_factory: Callable[
            ..., AbstractAsyncContextManager[ConfigPriceCollector]
        ],
    ) -> None:
        async with collector_factory("node-pool") as collector:
            result = await collector.get_latest_value()

        assert result == Price(value=Decimal(9), currency="USD")

    async def test_get_latest_value_unknown_node_pool(
        self,
        collector_factory: Callable[
            ..., AbstractAsyncContextManager[ConfigPriceCollector]
        ],
    ) -> None:
        async with collector_factory("unknown-node-pool") as collector:
            result = await collector.get_latest_value()

        assert result == Price()


class TestAWSNodePriceCollector:
    @pytest.fixture
    def pricing_client(self) -> mock.AsyncMock:
        return mock.AsyncMock()

    @pytest.fixture
    def ec2_client(self) -> mock.AsyncMock:
        return mock.AsyncMock()

    @pytest.fixture
    def collector_factory(
        self, pricing_client: AioBaseClient, ec2_client: AioBaseClient
    ) -> Callable[..., AbstractAsyncContextManager[AWSNodePriceCollector]]:
        @asynccontextmanager
        async def _create(
            is_spot: bool = False,
        ) -> AsyncIterator[AWSNodePriceCollector]:
            async with AWSNodePriceCollector(
                pricing_client=pricing_client,
                ec2_client=ec2_client,
                node_created_at=datetime.now(timezone.utc) - timedelta(hours=10),
                region="us-east-1",
                zone="us-east-1a",
                instance_type="p2.xlarge",
                is_spot=is_spot,
                interval_s=0.1,
            ) as result:
                assert isinstance(result, AWSNodePriceCollector)
                yield result

        return _create

    async def test_get_latest_value(
        self,
        collector_factory: Callable[
            ..., AbstractAsyncContextManager[AWSNodePriceCollector]
        ],
        pricing_client: mock.AsyncMock,
    ) -> None:
        pricing_client.get_products.return_value = {
            "PriceList": [
                json.dumps(
                    {
                        "terms": {
                            "OnDemand": {
                                "CGJXHFUSGE546RV6.JRTCKXETXF": {
                                    "priceDimensions": {
                                        "CGJXHFUSGE546RV6.JRTCKXETXF.6YS6EN2CT7": {
                                            "pricePerUnit": {"USD": "0.1"}
                                        }
                                    }
                                }
                            }
                        }
                    }
                )
            ]
        }

        async with collector_factory() as collector:
            result = await collector.get_latest_value()

        pricing_client.get_products.assert_awaited_once_with(
            ServiceCode="AmazonEC2",
            FormatVersion="aws_v1",
            Filters=[
                {"Type": "TERM_MATCH", "Field": "ServiceCode", "Value": "AmazonEC2"},
                {"Type": "TERM_MATCH", "Field": "locationType", "Value": "AWS Region"},
                {
                    "Type": "TERM_MATCH",
                    "Field": "location",
                    "Value": "US East (N. Virginia)",
                },
                {"Type": "TERM_MATCH", "Field": "operatingSystem", "Value": "Linux"},
                {"Type": "TERM_MATCH", "Field": "tenancy", "Value": "Shared"},
                {"Type": "TERM_MATCH", "Field": "capacitystatus", "Value": "Used"},
                {"Type": "TERM_MATCH", "Field": "preInstalledSw", "Value": "NA"},
                {"Type": "TERM_MATCH", "Field": "instanceType", "Value": "p2.xlarge"},
            ],
        )
        assert result == Price(currency="USD", value=Decimal(1))

    async def test_get_latest_value_with_multiple_prices(
        self,
        collector_factory: Callable[
            ..., AbstractAsyncContextManager[AWSNodePriceCollector]
        ],
        pricing_client: mock.AsyncMock,
    ) -> None:
        price_item = {
            "terms": {
                "OnDemand": {
                    "CGJXHFUSGE546RV6.JRTCKXETXF": {
                        "priceDimensions": {
                            "CGJXHFUSGE546RV6.JRTCKXETXF.6YS6EN2CT7": {
                                "pricePerUnit": {"USD": "0.1"}
                            }
                        }
                    }
                }
            }
        }
        pricing_client.get_products.return_value = {
            "PriceList": [json.dumps(price_item), json.dumps(price_item)]
        }

        async with collector_factory() as collector:
            result = await collector.get_latest_value()

        assert result == Price()

    async def test_get_latest_value_with_unsupported_currency(
        self,
        collector_factory: Callable[
            ..., AbstractAsyncContextManager[AWSNodePriceCollector]
        ],
        pricing_client: mock.AsyncMock,
    ) -> None:
        pricing_client.get_products.return_value = {
            "PriceList": [
                json.dumps(
                    {
                        "terms": {
                            "OnDemand": {
                                "CGJXHFUSGE546RV6.JRTCKXETXF": {
                                    "priceDimensions": {
                                        "CGJXHFUSGE546RV6.JRTCKXETXF.6YS6EN2CT7": {
                                            "pricePerUnit": {"UAH": "0.1"}
                                        }
                                    }
                                }
                            }
                        }
                    }
                )
            ]
        }

        async with collector_factory() as collector:
            result = await collector.get_latest_value()

        assert result == Price()

    async def test_get_latest_spot_value(
        self,
        collector_factory: Callable[
            ..., AbstractAsyncContextManager[AWSNodePriceCollector]
        ],
        ec2_client: mock.AsyncMock,
    ) -> None:
        ec2_client.describe_spot_price_history.return_value = {
            "SpotPriceHistory": [{"SpotPrice": "0.27"}]
        }

        async with collector_factory(is_spot=True) as collector:
            result = await collector.get_latest_value()

        assert result == Price(currency="USD", value=Decimal("2.7"))

        ec2_client.describe_spot_price_history.assert_awaited_once_with(
            AvailabilityZone="us-east-1a",
            InstanceTypes=["p2.xlarge"],
            ProductDescriptions=["Linux/UNIX"],
            StartTime=mock.ANY,
        )

    async def test_get_latest_spot_value_no_history(
        self,
        collector_factory: Callable[
            ..., AbstractAsyncContextManager[AWSNodePriceCollector]
        ],
        ec2_client: mock.AsyncMock,
    ) -> None:
        ec2_client.describe_spot_price_history.return_value = {"SpotPriceHistory": []}

        async with collector_factory(is_spot=True) as collector:
            result = await collector.get_latest_value()

        assert result == Price()


class TestGCPNodePriceCollector:
    @pytest.fixture
    def cluster(self) -> Cluster:
        return Cluster(
            name="default",
            status=ClusterStatus.DEPLOYED,
            created_at=datetime.now(),
            orchestrator=OrchestratorConfig(
                job_hostname_template="",
                job_internal_hostname_template="",
                job_fallback_hostname="",
                job_schedule_timeout_s=30,
                job_schedule_scale_up_timeout_s=30,
                resource_pool_types=[
                    ResourcePoolType(name="n1-highmem-8", cpu=8, memory=52 * 1024**3),
                    ResourcePoolType(
                        name="n1-highmem-8-4xk80",
                        cpu=8,
                        memory=52 * 1024**3,
                        gpu=4,
                        gpu_model="nvidia-tesla-k80",
                    ),
                    ResourcePoolType(
                        name="n1-highmem-8-1xv100",
                        cpu=8,
                        memory=52 * 1024**3,
                        gpu=1,
                        # not registered in google service skus fixture
                        gpu_model="nvidia-tesla-v100",
                    ),
                ],
            ),
        )

    @pytest.fixture
    def google_service_skus(self) -> dict[str, Any]:
        return {
            "skus": [
                {
                    "description": "N1 Predefined Instance Core running in Americas",
                    "category": {
                        "serviceDisplayName": "Compute Engine",
                        "resourceFamily": "Compute",
                        "resourceGroup": "N1Standard",
                        "usageType": "OnDemand",
                    },
                    "serviceRegions": ["us-central1"],
                    "pricingInfo": [
                        {
                            "pricingExpression": {
                                "tieredRates": [
                                    {
                                        "startUsageAmount": 0,
                                        "unitPrice": {
                                            "currencyCode": "USD",
                                            "units": "0",
                                            "nanos": 31611000,
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                },
                {
                    "description": "N1 Custom Instance Core running in Americas",
                    "category": {
                        "serviceDisplayName": "Compute Engine",
                        "resourceFamily": "Compute",
                        "resourceGroup": "N1Standard",
                        "usageType": "OnDemand",
                    },
                    "serviceRegions": ["us-central1"],
                    "pricingInfo": [
                        {
                            "pricingExpression": {
                                "tieredRates": [
                                    {
                                        "startUsageAmount": 0,
                                        "unitPrice": {
                                            "currencyCode": "USD",
                                            "units": "0",
                                            "nanos": 31611000,
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                },
                {
                    "description": "N1 Sole Tenancy Instance Core running in Americas",
                    "category": {
                        "serviceDisplayName": "Compute Engine",
                        "resourceFamily": "Compute",
                        "resourceGroup": "N1Standard",
                        "usageType": "OnDemand",
                    },
                    "serviceRegions": ["us-central1"],
                    "pricingInfo": [
                        {
                            "pricingExpression": {
                                "tieredRates": [
                                    {
                                        "startUsageAmount": 0,
                                        "unitPrice": {
                                            "currencyCode": "USD",
                                            "units": "0",
                                            "nanos": 31611000,
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                },
                {
                    "description": (
                        "Preemptible N1 Predefined Instance Core running in Americas"
                    ),
                    "category": {
                        "serviceDisplayName": "Compute Engine",
                        "resourceFamily": "Compute",
                        "resourceGroup": "N1Standard",
                        "usageType": "Preemptible",
                    },
                    "serviceRegions": ["us-central1"],
                    "pricingInfo": [
                        {
                            "pricingExpression": {
                                "tieredRates": [
                                    {
                                        "startUsageAmount": 0,
                                        "unitPrice": {
                                            "currencyCode": "USD",
                                            "units": "0",
                                            "nanos": 6655000,
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                },
                {
                    "description": "N1 Predefined Instance Ram running in Americas",
                    "category": {
                        "serviceDisplayName": "Compute Engine",
                        "resourceFamily": "Compute",
                        "resourceGroup": "N1Standard",
                        "usageType": "OnDemand",
                    },
                    "serviceRegions": ["us-central1"],
                    "pricingInfo": [
                        {
                            "pricingExpression": {
                                "tieredRates": [
                                    {
                                        "startUsageAmount": 0,
                                        "unitPrice": {
                                            "currencyCode": "USD",
                                            "units": "0",
                                            "nanos": 4237000,
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                },
                {
                    "description": (
                        "Preemptible N1 Predefined Instance Ram running in Americas"
                    ),
                    "category": {
                        "serviceDisplayName": "Compute Engine",
                        "resourceFamily": "Compute",
                        "resourceGroup": "N1Standard",
                        "usageType": "Preemptible",
                    },
                    "serviceRegions": ["us-central1"],
                    "pricingInfo": [
                        {
                            "pricingExpression": {
                                "tieredRates": [
                                    {
                                        "startUsageAmount": 0,
                                        "unitPrice": {
                                            "currencyCode": "USD",
                                            "units": "0",
                                            "nanos": 892000,
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                },
                {
                    "description": "Nvidia Tesla K80 GPU running in Americas",
                    "category": {
                        "serviceDisplayName": "Compute Engine",
                        "resourceFamily": "Compute",
                        "resourceGroup": "GPU",
                        "usageType": "OnDemand",
                    },
                    "serviceRegions": ["us-central1"],
                    "pricingInfo": [
                        {
                            "pricingExpression": {
                                "tieredRates": [
                                    {
                                        "startUsageAmount": 0,
                                        "unitPrice": {
                                            "currencyCode": "USD",
                                            "units": "0",
                                            "nanos": 450000000,
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                },
                {
                    "description": (
                        "Nvidia Tesla K80 GPU attached to preemptible "
                        "VMs running in Americas"
                    ),
                    "category": {
                        "serviceDisplayName": "Compute Engine",
                        "resourceFamily": "Compute",
                        "resourceGroup": "GPU",
                        "usageType": "Preemptible",
                    },
                    "serviceRegions": ["us-central1"],
                    "pricingInfo": [
                        {
                            "pricingExpression": {
                                "tieredRates": [
                                    {
                                        "startUsageAmount": 0,
                                        "unitPrice": {
                                            "currencyCode": "USD",
                                            "units": "0",
                                            "nanos": 135000000,
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                },
            ]
        }

    @pytest.fixture
    def collector_factory(
        self, cluster_holder: ClusterHolder, google_service_skus: dict[str, Any]
    ) -> Callable[..., AbstractContextManager[GCPNodePriceCollector]]:
        @contextmanager
        def _create(
            node_pool_name: str, instance_type: str, is_preemptible: bool = False
        ) -> Iterator[GCPNodePriceCollector]:
            result = GCPNodePriceCollector(
                cluster_holder=cluster_holder,
                service_account_path=Path("sa.json"),
                node_created_at=datetime.now(timezone.utc) - timedelta(hours=10),
                node_pool_name=node_pool_name,
                region="us-central1",
                instance_type=instance_type,
                is_preemptible=is_preemptible,
            )
            with mock.patch.object(result, "_client") as client:
                request = (
                    client.services.return_value.skus.return_value.list.return_value
                )
                request.execute.return_value = google_service_skus
                yield result

        return _create

    async def test_get_latest_value_cpu_instance(
        self,
        collector_factory: Callable[..., AbstractContextManager[GCPNodePriceCollector]],
    ) -> None:
        with collector_factory("n1-highmem-8", "n1-highmem-8") as collector:
            result = await collector.get_latest_value()
            assert result == Price(value=Decimal("4.73"), currency="USD")

    async def test_get_latest_value_cpu_instance_preemptible(
        self,
        collector_factory: Callable[..., AbstractContextManager[GCPNodePriceCollector]],
    ) -> None:
        with collector_factory("n1-highmem-8", "n1-highmem-8", True) as collector:
            result = await collector.get_latest_value()
            assert result == Price(value=Decimal(1), currency="USD")

    async def test_get_latest_value_gpu_instance(
        self,
        collector_factory: Callable[..., AbstractContextManager[GCPNodePriceCollector]],
    ) -> None:
        with collector_factory("n1-highmem-8-4xk80", "n1-highmem-8") as collector:
            result = await collector.get_latest_value()
            assert result == Price(value=Decimal("22.73"), currency="USD")

    async def test_get_latest_value_gpu_instance_preemptible(
        self,
        collector_factory: Callable[..., AbstractContextManager[GCPNodePriceCollector]],
    ) -> None:
        with collector_factory("n1-highmem-8-4xk80", "n1-highmem-8", True) as collector:
            result = await collector.get_latest_value()
            assert result == Price(value=Decimal("6.4"), currency="USD")

    async def test_get_latest_value_unknown_instance_type(
        self,
        collector_factory: Callable[..., AbstractContextManager[GCPNodePriceCollector]],
    ) -> None:
        with (
            collector_factory("n1-highmem-8", "unknown", True) as collector,
            pytest.raises(AssertionError, match=r"Found prices only for: \[\]"),
        ):
            await collector.get_latest_value()

    async def test_get_latest_value_unknown_gpu(
        self,
        collector_factory: Callable[..., AbstractContextManager[GCPNodePriceCollector]],
    ) -> None:
        with (
            collector_factory("n1-highmem-8-1xv100", "n1-highmem-8", True) as collector,
            pytest.raises(AssertionError, match=r"Found prices only for: \[CPU, RAM\]"),
        ):
            await collector.get_latest_value()


class TestAzureNodePriceCollector:
    @pytest.fixture
    def prices_client_factory(
        self,
        aiohttp_client: Any,
    ) -> Callable[..., Awaitable[aiohttp.ClientSession]]:
        async def _create(
            payload: dict[str, Any], expected_filter: str = ""
        ) -> aiohttp.ClientSession:
            async def get_prices(request: web.Request) -> web.Response:
                if expected_filter:
                    assert request.query["$filter"] == expected_filter
                return web.json_response(payload)

            app = web.Application()
            app.router.add_get("/api/retail/prices", get_prices)
            return await aiohttp_client(app)

        return _create

    @pytest.fixture
    def collector_factory(self) -> Callable[..., AzureNodePriceCollector]:
        def _create(
            prices_client: aiohttp.ClientSession,
            instance_type: str,
            is_spot: bool = False,
        ) -> AzureNodePriceCollector:
            return AzureNodePriceCollector(
                prices_client=prices_client,
                prices_url=URL(""),
                node_created_at=datetime.now(timezone.utc) - timedelta(hours=10),
                region="eastus",
                instance_type=instance_type,
                is_spot=is_spot,
            )

        return _create

    async def test_get_latest_value(
        self,
        prices_client_factory: Callable[..., Awaitable[aiohttp.ClientSession]],
        collector_factory: Callable[..., AzureNodePriceCollector],
    ) -> None:
        prices_client = await prices_client_factory(
            {
                "Items": [
                    {"currencyCode": "USD", "retailPrice": "0.9", "productName": ""}
                ]
            },
            (
                "serviceName eq 'Virtual Machines' "
                "and priceType eq 'Consumption' "
                "and armRegionName eq 'eastus' "
                "and armSkuName eq 'Standard_NC6' "
                "and skuName eq 'NC6'"
            ),
        )
        collector = collector_factory(prices_client, "Standard_NC6")
        result = await collector.get_latest_value()

        assert result == Price(value=Decimal(9), currency="USD")

    async def test_get_latest_value_general_purpose_instance(
        self,
        prices_client_factory: Callable[..., Awaitable[aiohttp.ClientSession]],
        collector_factory: Callable[..., AzureNodePriceCollector],
    ) -> None:
        prices_client = await prices_client_factory(
            {
                "Items": [
                    {"currencyCode": "USD", "retailPrice": 0.096, "productName": ""}
                ]
            },
            (
                "serviceName eq 'Virtual Machines' "
                "and priceType eq 'Consumption' "
                "and armRegionName eq 'eastus' "
                "and armSkuName eq 'Standard_D2_v3' "
                "and skuName eq 'D2 v3'"
            ),
        )
        collector = collector_factory(prices_client, "Standard_D2s_v3")
        result = await collector.get_latest_value()

        assert result == Price(value=Decimal("0.96"), currency="USD")

    async def test_get_latest_value_multiple_prices(
        self,
        prices_client_factory: Callable[..., Awaitable[aiohttp.ClientSession]],
        collector_factory: Callable[..., AzureNodePriceCollector],
    ) -> None:
        prices_client = await prices_client_factory(
            {
                "Items": [
                    {"currencyCode": "USD", "retailPrice": 0.9, "productName": ""},
                    {"currencyCode": "USD", "retailPrice": 0.9, "productName": ""},
                ]
            },
        )
        collector = collector_factory(prices_client, "Standard_NC6")
        result = await collector.get_latest_value()

        assert result == Price()

    async def test_get_latest_value_filters_windows_os(
        self,
        prices_client_factory: Callable[..., Awaitable[aiohttp.ClientSession]],
        collector_factory: Callable[..., AzureNodePriceCollector],
    ) -> None:
        prices_client = await prices_client_factory(
            {
                "Items": [
                    {"currencyCode": "USD", "retailPrice": 0.9, "productName": ""},
                    {
                        "currencyCode": "USD",
                        "retailPrice": 0.9,
                        "productName": "Windows",
                    },
                ]
            },
        )
        collector = collector_factory(prices_client, "Standard_NC6")
        result = await collector.get_latest_value()

        assert result == Price(value=Decimal(9), currency="USD")

    async def test_get_latest_value_unknown_instance_type(
        self,
        prices_client_factory: Callable[..., Awaitable[aiohttp.ClientSession]],
        collector_factory: Callable[..., AzureNodePriceCollector],
    ) -> None:
        prices_client = await prices_client_factory({"Items": []})
        collector = collector_factory(prices_client, "Standard_NC6")
        result = await collector.get_latest_value()

        assert result == Price()

    async def test_get_latest_spot_value(
        self,
        prices_client_factory: Callable[..., Awaitable[aiohttp.ClientSession]],
        collector_factory: Callable[..., AzureNodePriceCollector],
    ) -> None:
        prices_client = await prices_client_factory(
            {"Items": [{"currencyCode": "USD", "retailPrice": 0.9, "productName": ""}]},
            (
                "serviceName eq 'Virtual Machines' "
                "and priceType eq 'Consumption' "
                "and armRegionName eq 'eastus' "
                "and armSkuName eq 'Standard_NC6' "
                "and skuName eq 'NC6 Spot'"
            ),
        )
        collector = collector_factory(prices_client, "Standard_NC6", is_spot=True)
        result = await collector.get_latest_value()

        assert result == Price(value=Decimal(9), currency="USD")


class TestPodCreditsCollector:
    @pytest.fixture
    def cluster(self) -> Cluster:
        return Cluster(
            name="default",
            status=ClusterStatus.DEPLOYED,
            created_at=datetime.now(),
            orchestrator=OrchestratorConfig(
                job_hostname_template="",
                job_internal_hostname_template="",
                job_fallback_hostname="",
                job_schedule_timeout_s=30,
                job_schedule_scale_up_timeout_s=30,
                resource_presets=[
                    ResourcePreset(
                        name="test-preset",
                        cpu=1,
                        memory=1024**3,
                        credits_per_hour=Decimal(10),
                    )
                ],
            ),
        )

    @pytest.fixture
    def kube_client_factory(self) -> Callable[..., mock.AsyncMock]:
        def _create(pods: list[Pod]) -> mock.AsyncMock:
            async def get_pods(
                namespace: str | None = None,
                field_selector: str | None = None,
                label_selector: str | None = None,
            ) -> Sequence[Pod]:
                assert namespace is None
                assert label_selector == "preset"
                assert field_selector == "spec.nodeName=minikube,status.phase!=Pending"
                return pods

            result = mock.AsyncMock(spec=KubeClient)
            result.get_pods.side_effect = get_pods
            return result

        return _create

    @pytest.fixture
    def collector_factory(
        self,
        cluster_holder: ClusterHolder,
        kube_client_factory: Callable[..., KubeClient],
    ) -> Callable[..., PodCreditsCollector]:
        def _create(pods: list[Pod]) -> PodCreditsCollector:
            kube_client = kube_client_factory(pods)
            return PodCreditsCollector(
                kube_client=kube_client,
                cluster_holder=cluster_holder,
                node_name="minikube",
                pod_preset_label="preset",
            )

        return _create

    async def test_get_latest_value__running(
        self, collector_factory: Callable[..., PodCreditsCollector]
    ) -> None:
        collector = collector_factory(
            pods=[
                Pod(
                    metadata=Metadata(
                        name="test",
                        labels={"preset": "test-preset"},
                        creation_timestamp=datetime.now(UTC),
                    ),
                    status=PodStatus(
                        phase=PodPhase.RUNNING,
                        container_statuses=[
                            ContainerStatus(
                                {
                                    "running": {
                                        "startedAt": (
                                            datetime.now(UTC) - timedelta(hours=1)
                                        ).isoformat()
                                    }
                                }
                            )
                        ],
                    ),
                )
            ],
        )
        result = await collector.get_latest_value()

        assert result == {"test": Decimal(10)}

    async def test_get_latest_value__terminated(
        self, collector_factory: Callable[..., PodCreditsCollector]
    ) -> None:
        collector = collector_factory(
            pods=[
                Pod(
                    metadata=Metadata(
                        name="test",
                        labels={"preset": "test-preset"},
                        creation_timestamp=datetime.now(UTC),
                    ),
                    status=PodStatus(
                        phase=PodPhase.SUCCEEDED,
                        container_statuses=[
                            ContainerStatus(
                                {
                                    "terminated": {
                                        "startedAt": (
                                            datetime.now(UTC) - timedelta(hours=1.5)
                                        ).isoformat(),
                                        "finishedAt": (
                                            datetime.now(UTC) - timedelta(hours=0.5)
                                        ).isoformat(),
                                    }
                                }
                            )
                        ],
                    ),
                )
            ],
        )
        result = await collector.get_latest_value()

        assert result == {"test": Decimal(10)}

    async def test_get_latest_value__unknown_preset(
        self, collector_factory: Callable[..., PodCreditsCollector]
    ) -> None:
        collector = collector_factory(
            pods=[
                Pod(
                    metadata=Metadata(
                        name="test",
                        labels={"preset": "unknown-preset"},
                        creation_timestamp=datetime.now(UTC),
                    ),
                    status=PodStatus(
                        phase=PodPhase.RUNNING,
                        container_statuses=[
                            ContainerStatus(
                                {
                                    "running": {
                                        "startedAt": datetime.now(UTC).isoformat()
                                    }
                                }
                            )
                        ],
                    ),
                )
            ],
        )
        result = await collector.get_latest_value()

        assert result == {}

    async def test_get_latest_value__no_pods(
        self,
        collector_factory: Callable[..., PodCreditsCollector],
    ) -> None:
        collector = collector_factory(pods=[])
        result = await collector.get_latest_value()

        assert result == {}


class TestNodeEnergyConsumptionCollector:
    @pytest.fixture
    def cluster(self) -> Cluster:
        return Cluster(
            name="default",
            status=ClusterStatus.DEPLOYED,
            created_at=datetime.now(),
            timezone=timezone.utc,
            cloud_provider=OnPremCloudProvider(
                storage=None,
                node_pools=[
                    NodePool(
                        name="node-pool",
                        cpu_min_watts=10.5,
                        cpu_max_watts=110.0,
                    ),
                ],
            ),
            energy=EnergyConfig(
                co2_grams_eq_per_kwh=1000,
                schedules=[
                    EnergySchedule(
                        name="default",
                        price_per_kwh=Decimal("10"),
                    ),
                    EnergySchedule(
                        name="night",
                        price_per_kwh=Decimal("5"),
                        periods=[
                            EnergySchedulePeriod(
                                1,
                                start_time=time(0, tzinfo=timezone.utc),
                                end_time=time(5, 59, tzinfo=timezone.utc),
                            )
                        ],
                    ),
                ],
            ),
        )

    async def test_get_latest_value(self, cluster_holder: ClusterHolder) -> None:
        current_time = datetime(
            year=2023,
            month=1,
            day=30,
            hour=5,
            minute=59,
            second=59,
            tzinfo=ZoneInfo("UTC"),
        )
        async with NodeEnergyConsumptionCollector(
            cluster_holder=cluster_holder,
            node_pool_name="node-pool",
            current_time_factory=lambda _: current_time,
        ) as collector:
            value = await collector.get_latest_value()
            assert value.cpu_min_watts == 10.5
            assert value.cpu_max_watts == 110.0
            assert value.co2_grams_eq_per_kwh == 1000.0
            assert value.price_per_kwh == 5

    async def test_get_latest_value__default(
        self, cluster_holder: ClusterHolder
    ) -> None:
        current_time = datetime(
            year=2023, month=1, day=31, hour=5, tzinfo=ZoneInfo("UTC")
        )
        async with NodeEnergyConsumptionCollector(
            cluster_holder=cluster_holder,
            node_pool_name="node-pool",
            current_time_factory=lambda _: current_time,
        ) as collector:
            value = await collector.get_latest_value()
            assert value.cpu_min_watts == 10.5
            assert value.cpu_max_watts == 110.0
            assert value.co2_grams_eq_per_kwh == 1000.0
            assert value.price_per_kwh == 10

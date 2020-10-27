import asyncio
import json
from contextlib import suppress
from typing import AsyncIterator, Callable, Iterator
from unittest import mock

import pytest
from aiobotocore.client import AioBaseClient
from platform_config_client import (
    Cluster,
    ConfigClient,
    OrchestratorConfig,
    ResourcePoolType,
)

from platform_reports.kube_client import (
    Container,
    KubeClient,
    Metadata,
    Pod,
    PodPhase,
    PodStatus,
    Resources,
)
from platform_reports.metrics import (
    AWSNodePriceCollector,
    Collector,
    PodPriceCollector,
    Price,
)


class TestCollector:
    @pytest.fixture
    def collector(self) -> Collector[Price]:
        return Collector(Price(), interval_s=0.1)

    @pytest.fixture
    def price_factory(self, collector: Collector[Price]) -> Iterator[mock.Mock]:
        price = Price(currency="USD", value=1)
        with mock.patch.object(
            collector, "get_latest_value", return_value=price,
        ) as mock_method:
            yield mock_method

    async def test_update(
        self, collector: Collector[Price], price_factory: mock.Mock
    ) -> None:
        task = asyncio.create_task(await collector.start())

        await asyncio.sleep(0.3)

        assert collector.current_value == Price(currency="USD", value=1)
        assert price_factory.call_count >= 3

        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


class TestAWSNodePriceCollector:
    @pytest.fixture
    def pricing_client(self) -> mock.AsyncMock:
        return mock.AsyncMock()

    @pytest.fixture
    async def price_collector(
        self, pricing_client: AioBaseClient
    ) -> AsyncIterator[AWSNodePriceCollector]:
        async with AWSNodePriceCollector(
            pricing_client=pricing_client,
            region="us-east-1",
            instance_type="p2.xlarge",
            interval_s=0.1,
        ) as result:
            assert isinstance(result, AWSNodePriceCollector)
            yield result

    async def test_get_latest_price_per_hour(
        self, price_collector: AWSNodePriceCollector, pricing_client: mock.AsyncMock
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

        result = await price_collector.get_latest_value()

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
        assert result == Price(currency="USD", value=0.1)

    async def test_get_latest_price_per_hour_with_multiple_prices(
        self, price_collector: AWSNodePriceCollector, pricing_client: mock.AsyncMock
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

        result = await price_collector.get_latest_value()

        assert result == Price()

    async def test_get_latest_price_per_hour_with_unsupported_currency(
        self, price_collector: AWSNodePriceCollector, pricing_client: mock.AsyncMock
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

        result = await price_collector.get_latest_value()

        assert result == Price()


class TestPodPriceCollector:
    @pytest.fixture
    def config_client_factory(self) -> Callable[..., mock.AsyncMock]:
        def _create(node_resources: Resources) -> mock.AsyncMock:
            result = mock.AsyncMock(spec=ConfigClient)
            result.get_cluster.return_value = Cluster(
                name="minikube",
                orchestrator=OrchestratorConfig(
                    job_hostname_template="",
                    job_fallback_hostname="",
                    resource_pool_types=[
                        ResourcePoolType(
                            name="minikube-node-pool",
                            cpu=node_resources.cpu_m / 1000,
                            memory_mb=node_resources.memory_mb,
                            gpu=node_resources.gpu or None,
                        )
                    ],
                ),
            )
            return result

        return _create

    @pytest.fixture
    def kube_client_factory(self) -> Callable[..., mock.AsyncMock]:
        def _create(**pod_resources: Resources) -> mock.AsyncMock:
            result = mock.AsyncMock(spec=KubeClient)
            result.get_pods.return_value = [
                Pod(
                    metadata=Metadata(name=name),
                    status=PodStatus(phase=PodPhase.RUNNING),
                    containers=[Container(name=name, resource_requests=resources)],
                )
                for name, resources in pod_resources.items()
            ]
            return result

        return _create

    @pytest.fixture
    def node_price_collector(self) -> mock.AsyncMock:
        result = mock.Mock(spec=Collector[Price])
        type(result).current_value = mock.PropertyMock(
            return_value=Price(value=1.0, currency="USD")
        )
        return result

    @pytest.fixture
    def collector_factory(
        self,
        config_client_factory: Callable[..., ConfigClient],
        kube_client_factory: Callable[..., KubeClient],
        node_price_collector: Collector[Price],
    ) -> Callable[..., PodPriceCollector]:
        def _create(node: Resources, **pod_resources: Resources) -> PodPriceCollector:
            config_client = config_client_factory(node)
            kube_client = kube_client_factory(**pod_resources)
            return PodPriceCollector(
                config_client=config_client,
                kube_client=kube_client,
                node_price_collector=node_price_collector,
                cluster_name="default",
                node_name="minikube",
                node_pool_name="minikube-node-pool",
                jobs_namespace="platform-jobs",
                job_label="job",
            )

        return _create

    async def test_get_latest_price_per_hour(
        self, collector_factory: Callable[..., PodPriceCollector],
    ) -> None:
        collector = collector_factory(
            node=Resources(cpu_m=1000, memory_mb=4096),
            job=Resources(cpu_m=100, memory_mb=100),
        )
        result = await collector.get_latest_value()

        assert result == {"job": Price(value=0.1, currency="USD")}

    async def test_get_latest_price_per_hour_high_cpu(
        self, collector_factory: Callable[..., PodPriceCollector],
    ) -> None:
        collector = collector_factory(
            node=Resources(cpu_m=1000, memory_mb=4096),
            job=Resources(cpu_m=1000, memory_mb=100),
        )
        result = await collector.get_latest_value()

        assert result == {"job": Price(value=1.0, currency="USD")}

    async def test_get_latest_price_per_hour_high_memory(
        self, collector_factory: Callable[..., PodPriceCollector],
    ) -> None:
        collector = collector_factory(
            node=Resources(cpu_m=1000, memory_mb=4096),
            job=Resources(cpu_m=100, memory_mb=4096),
        )
        result = await collector.get_latest_value()

        assert result == {"job": Price(value=1.0, currency="USD")}

    async def test_get_latest_price_per_hour_gpu(
        self, collector_factory: Callable[..., PodPriceCollector],
    ) -> None:
        collector = collector_factory(
            node=Resources(cpu_m=1000, memory_mb=4096, gpu=4),
            job=Resources(cpu_m=100, memory_mb=100, gpu=3),
        )
        result = await collector.get_latest_value()

        assert result == {"job": Price(value=0.75, currency="USD")}

    async def test_get_latest_price_per_memory_overused(
        self, collector_factory: Callable[..., PodPriceCollector],
    ) -> None:
        collector = collector_factory(
            node=Resources(cpu_m=1000, memory_mb=4096),
            job=Resources(cpu_m=100, memory_mb=5000),
        )
        result = await collector.get_latest_value()

        assert result == {"job": Price(value=1.0, currency="USD")}

    async def test_get_latest_price_per_hour_multiple_pods(
        self, collector_factory: Callable[..., PodPriceCollector],
    ) -> None:
        collector = collector_factory(
            node=Resources(cpu_m=1000, memory_mb=4096),
            job1=Resources(cpu_m=100, memory_mb=100),
            job2=Resources(cpu_m=200, memory_mb=100),
        )
        result = await collector.get_latest_value()

        assert result == {
            "job1": Price(value=0.1, currency="USD"),
            "job2": Price(value=0.2, currency="USD"),
        }

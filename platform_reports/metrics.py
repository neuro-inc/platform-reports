from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from importlib.resources import files
from pathlib import Path
from types import TracebackType
from typing import Any, Generic, TypeVar

import aiohttp
from aiobotocore.client import AioBaseClient
from google.oauth2.service_account import Credentials
from googleapiclient import discovery
from neuro_config_client import ConfigClient
from neuro_logging import new_trace_cm, trace_cm
from neuro_sdk import Client as ApiClient, JobDescription, ResourceNotFound
from yarl import URL

from .kube_client import KubeClient

logger = logging.getLogger(__name__)


GOOGLE_COMPUTE_ENGINE_ID = "services/6F81-5844-456A"


@dataclass(frozen=True)
class Price:
    currency: str = ""
    value: Decimal = Decimal()

    def __str__(self) -> str:
        return f"{self.value} {self.currency}" if self.currency else str(self.value)

    def __repr__(self) -> str:
        return f"{self.value!r} {self.currency}" if self.currency else f"{self.value!r}"


@dataclass(frozen=True)
class CPUPowerConsuption:
    min_consumption: Decimal = Decimal()
    max_consumption: Decimal = Decimal()
    co2_grams_eq_per_kwh: Decimal = Decimal()


_TValue = TypeVar("_TValue")


class Collector(Generic[_TValue]):
    def __init__(self, initial_value: _TValue, interval_s: float = 3600) -> None:
        self._interval_s = interval_s
        self._value = initial_value

    @property
    def current_value(self) -> _TValue:
        return self._value

    async def get_latest_value(self) -> _TValue:
        return self._value

    async def start(self) -> Awaitable[None]:
        self._value = await self.get_latest_value()
        logger.info("Updated value to %s", self._value)
        return self._update()

    async def _update(self) -> None:
        while True:
            try:
                logger.info("Next update will be in %s seconds", self._interval_s)
                await asyncio.sleep(self._interval_s)

                async with new_trace_cm(
                    name=f"{self.__class__.__name__}.get_latest_value"
                ):
                    self._value = await self.get_latest_value()

                logger.info("Updated value to %s", self._value)
            except asyncio.CancelledError:
                raise
            except Exception as ex:
                logger.warning(
                    "Unexpected error ocurred during value update", exc_info=ex
                )

    async def __aenter__(self) -> Collector[_TValue]:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        pass


class _NodePriceCollector(Collector[Price]):
    def __init__(
        self, node_created_at: datetime, initial_value: Price, interval_s: float = 3600
    ) -> None:
        super().__init__(initial_value, interval_s)

        self._node_created_at = node_created_at

    async def get_latest_value(self) -> Price:
        node_up_time = datetime.now(timezone.utc) - self._node_created_at
        price_per_hour = await self.get_price_per_hour()
        return Price(
            currency=price_per_hour.currency,
            value=round(
                price_per_hour.value * int(node_up_time.total_seconds()) / 3600, 2
            ),
        )

    async def get_price_per_hour(self) -> Price:
        return Price()


class ConfigPriceCollector(_NodePriceCollector):
    def __init__(
        self,
        config_client: ConfigClient,
        cluster_name: str,
        node_created_at: datetime,
        node_pool_name: str,
        interval_s: float = 60,
    ) -> None:
        super().__init__(node_created_at, Price(), interval_s)

        self._config_client = config_client
        self._cluster_name = cluster_name
        self._node_pool_name = node_pool_name
        self._client: Any = None

    async def get_price_per_hour(self) -> Price:
        cluster = await self._config_client.get_cluster(self._cluster_name)
        assert cluster.orchestrator is not None
        for resource_pool in cluster.orchestrator.resource_pool_types:
            if resource_pool.name == self._node_pool_name:
                return Price(
                    value=resource_pool.price, currency=resource_pool.currency or ""
                )

        return Price()


class AWSNodePriceCollector(_NodePriceCollector):
    def __init__(
        self,
        pricing_client: AioBaseClient,
        ec2_client: AioBaseClient,
        node_created_at: datetime,
        region: str,
        instance_type: str,
        zone: str,
        is_spot: bool,
        interval_s: float = 3600,
    ) -> None:
        super().__init__(node_created_at, Price(), interval_s)

        self._pricing_client = pricing_client
        self._ec2_client = ec2_client
        self._region = region
        self._region_long_name = ""
        self._zone = zone
        self._instance_type = instance_type
        self._is_spot = is_spot

    async def __aenter__(self) -> Collector[Price]:
        await super().__aenter__()
        region_long_names = self._get_region_long_names()
        self._region_long_name = region_long_names[self._region]
        logger.info(
            "Initialized AWS price collector for %s instance in %s region",
            self._instance_type,
            self._region_long_name,
        )
        return self

    # The pricing API requires human readable names for some reason
    def _get_region_long_names(self) -> dict[str, str]:
        result: dict[str, str] = {}
        # https://github.com/boto/botocore/blob/master/botocore/data/endpoints.json
        root = files("botocore")
        endpoints_path = root / "data/endpoints.json"
        with endpoints_path.open("r", encoding="utf-8") as f:
            endpoints = json.load(f)
        for partition in endpoints["partitions"]:
            regions = partition["regions"]
            for region in regions:
                result[region] = regions[region]["description"]
        # The Osaka region is special and is not on the list of endpoints in botocore
        result["ap-northeast-3"] = "Asia Pacific (Osaka-Local)"
        return result

    async def get_price_per_hour(self) -> Price:
        if self._is_spot:
            return await self._get_latest_spot_price()
        return await self._get_latest_on_demand_price()

    async def _get_latest_on_demand_price(self) -> Price:
        response = await self._pricing_client.get_products(
            ServiceCode="AmazonEC2",
            FormatVersion="aws_v1",
            Filters=[
                self._create_filter("ServiceCode", "AmazonEC2"),
                self._create_filter("locationType", "AWS Region"),
                self._create_filter("location", self._region_long_name),
                self._create_filter("operatingSystem", "Linux"),
                self._create_filter("tenancy", "Shared"),
                self._create_filter("capacitystatus", "Used"),
                self._create_filter("preInstalledSw", "NA"),
                self._create_filter("instanceType", self._instance_type),
            ],
        )
        if len(response["PriceList"]) != 1:
            logger.warning(
                "AWS returned %d products in %s region, cannot determine node price",
                len(response["PriceList"]),
                self._region_long_name,
            )
            return Price()
        price_list = json.loads(response["PriceList"][0])
        on_demand = price_list["terms"]["OnDemand"]
        id1 = next(iter(on_demand))
        id2 = next(iter(on_demand[id1]["priceDimensions"]))
        price_per_unit = on_demand[id1]["priceDimensions"][id2]["pricePerUnit"]
        if "USD" in price_per_unit:
            return Price(currency="USD", value=Decimal(str(price_per_unit["USD"])))
        logger.warning(
            "AWS product currencies are not supported: %s", list(price_per_unit.keys())
        )
        return Price()

    def _create_filter(self, field: str, value: str) -> dict[str, str]:
        return {"Type": "TERM_MATCH", "Field": field, "Value": value}

    async def _get_latest_spot_price(self) -> Price:
        response = await self._ec2_client.describe_spot_price_history(
            AvailabilityZone=self._zone,
            InstanceTypes=[self._instance_type],
            ProductDescriptions=["Linux/UNIX"],
            StartTime=datetime.utcnow(),
        )
        history = response["SpotPriceHistory"]
        if len(history) == 0:
            logger.warning(
                "AWS didn't return spot price history for %s instance in %s zone",
                self._instance_type,
                self._zone,
            )
            return Price()
        return Price(currency="USD", value=Decimal(str(history[0]["SpotPrice"])))


class AzureNodePriceCollector(_NodePriceCollector):
    def __init__(
        self,
        prices_client: aiohttp.ClientSession,
        prices_url: URL,
        node_created_at: datetime,
        region: str,
        instance_type: str,
        is_spot: bool,
        interval_s: float = 3600,
    ) -> None:
        super().__init__(node_created_at, Price(), interval_s)

        self._prices_client = prices_client
        self._prices_url = prices_url
        self._region = region
        self._instance_type = instance_type
        self._sku_name = " ".join(instance_type.split("_")[1:])

        if is_spot:
            self._sku_name += " Spot"
        else:
            # For on-demand instances base instance price should be taken.
            # e. g.: Standard_D2_v3 instead of Standard_D2s_v3
            match = re.search(r"^(D\d\d?)a?s? (v\d)$", self._sku_name)
            if match:
                self._instance_type = f"Standard_{match.group(1)}_{match.group(2)}"
                self._sku_name = f"{match.group(1)} {match.group(2)}"

    async def get_price_per_hour(self) -> Price:
        response = await self._prices_client.get(
            (self._prices_url / "api/retail/prices").with_query(
                {
                    "$filter": " ".join(
                        [
                            "serviceName eq 'Virtual Machines'",
                            "and priceType eq 'Consumption'",
                            f"and armRegionName eq '{self._region}'",
                            f"and armSkuName eq '{self._instance_type}'",
                            f"and skuName eq '{self._sku_name}'",
                        ]
                    )
                }
            )
        )
        response.raise_for_status()
        payload = await response.json()
        # filter out Windows instances
        items = [
            i for i in payload["Items"] if "windows" not in i["productName"].lower()
        ]
        if len(items) != 1:
            logger.warning(
                "Azure returned %d products in %s region, cannot determine node price",
                len(items),
                self._region,
            )
            return Price()
        return Price(
            currency=items[0]["currencyCode"],
            value=Decimal(str(items[0]["retailPrice"])),
        )


class GCPNodePriceCollector(_NodePriceCollector):
    def __init__(
        self,
        config_client: ConfigClient,
        service_account_path: Path,
        node_created_at: datetime,
        node_pool_name: str,
        cluster_name: str,
        region: str,
        instance_type: str,
        is_preemptible: bool,
        interval_s: float = 3600,
    ) -> None:
        super().__init__(node_created_at, Price(), interval_s)

        self._config_client = config_client
        self._service_account_path = service_account_path
        self._cluster_name = cluster_name
        self._node_pool_name = node_pool_name
        self._region = region
        self._instance_family = self._get_instance_family(instance_type)
        self._usage_type = self._get_usage_type(is_preemptible)
        self._loop = asyncio.get_event_loop()
        self._client: Any = None

    async def __aenter__(self) -> "Collector[Price]":
        self._client = await self._loop.run_in_executor(None, self._create_client)
        return self

    def _create_client(self) -> Any:
        sa_credentials = Credentials.from_service_account_file(
            str(self._service_account_path)
        )
        return discovery.build("cloudbilling", "v1", credentials=sa_credentials)

    def _get_instance_family(self, instance_type: str) -> str:
        # Can be n1, n2, n2d, e2
        return instance_type.split("-")[0].lower()

    def _get_usage_type(self, is_preemptible: bool) -> str:
        return "preemptible" if is_preemptible else "ondemand"

    async def get_price_per_hour(self) -> Price:
        cluster = await self._config_client.get_cluster(self._cluster_name)
        assert cluster.orchestrator is not None
        resource_pools = {r.name: r for r in cluster.orchestrator.resource_pool_types}
        if self._node_pool_name not in resource_pools:
            return Price(currency="USD")
        resource_pool = resource_pools[self._node_pool_name]
        return await self._get_instance_price_per_hour(
            resource_pool.cpu,
            resource_pool.memory,
            resource_pool.gpu or 0,
            resource_pool.gpu_model or "",
        )

    async def _get_instance_price_per_hour(
        self, cpu: float, memory: int, gpu: int, gpu_model: str
    ) -> Price:
        prices_in_nanos: dict[str, Decimal] = {}
        expected_prices_count = bool(cpu) + bool(memory) + bool(gpu)
        gpu_model = gpu_model.replace("-", " ").lower()
        async for sku in self._get_service_skus():
            # The only reliable way to match instance type with sku is through
            # sku description field. Other sku fields don't contain instance type
            # specific fields.
            sku_description = sku["description"].lower()
            sku_description_words = set(sku_description.split())
            sku_usage_type = sku["category"]["usageType"].lower()

            # Calculate price for CPU and RAM
            if (
                self._instance_family in sku_description_words
                and self._usage_type == sku_usage_type
            ):
                price_in_nanos = self._get_price_in_nanos(sku)
                if sku_description_words.intersection(("core", "cpu", "vcpu")):
                    assert "cpu" not in prices_in_nanos
                    prices_in_nanos["cpu"] = price_in_nanos * Decimal(str(cpu))
                if "ram" in sku_description_words:
                    assert "ram" not in prices_in_nanos
                    prices_in_nanos["ram"] = price_in_nanos * memory / 1024**3

            # Calculate price for the attached GPU
            if (
                gpu
                and gpu_model in sku_description
                and self._usage_type == sku_usage_type
            ):
                price_in_nanos = self._get_price_in_nanos(sku)
                assert "gpu" not in prices_in_nanos
                prices_in_nanos["gpu"] = gpu * price_in_nanos

            if len(prices_in_nanos) == expected_prices_count:
                break
        assert (
            len(prices_in_nanos) == expected_prices_count
        ), f"Found prices only for: [{', '.join(prices_in_nanos.keys()).upper()}]"
        return Price(
            value=sum(prices_in_nanos.values(), Decimal()) / 10**9, currency="USD"
        )

    async def _get_service_skus(self) -> AsyncIterator[dict[str, Any]]:
        next_page_token: str | None = ""
        while next_page_token is not None:
            async with trace_cm(name="list_service_skus"):
                response = await self._loop.run_in_executor(
                    None, self._list_service_skus, next_page_token
                )
            for sku in response["skus"]:
                if (
                    sku["category"]["resourceFamily"] == "Compute"
                    and sku["category"]["usageType"] in ("OnDemand", "Preemptible")
                    and self._region in sku["serviceRegions"]
                ):
                    yield sku
            next_page_token = response.get("nextPageToken") or None

    def _list_service_skus(self, next_page_token: str) -> Any:
        request = (
            self._client.services()
            .skus()
            .list(
                parent=GOOGLE_COMPUTE_ENGINE_ID,
                currencyCode="USD",
                pageToken=next_page_token,
            )
        )
        return request.execute()

    def _get_price_in_nanos(self, sku: dict[str, Any]) -> Decimal:
        # PricingInfo can contain multiple objects. Each corresponds to separate
        # time interval. But as long as we don't provide time constraints
        # in Google API query we will receive only latest value.
        tiered_rates = sku["pricingInfo"][0]["pricingExpression"]["tieredRates"]
        # TieredRates can contain multiple values with it's own startUsageAmount.
        # Usage is priced at this rate only after the startUsageAmount is reached.
        # OnDemand and Preemptible instance prices don't depend on usage amount,
        # so there will be only one rate.
        tiered_rate = next(iter(t for t in tiered_rates if t["startUsageAmount"] == 0))
        unit_price = tiered_rate["unitPrice"]
        # UnitPrice contains price in nanos which is 1 USD * 10^-9
        return Decimal(str(unit_price["units"])) * 10**9 + unit_price["nanos"]


class PodCreditsCollector(Collector[Mapping[str, Decimal]]):
    def __init__(
        self,
        kube_client: KubeClient,
        api_client: ApiClient,
        node_name: str,
        jobs_namespace: str,
        job_label: str,
        interval_s: float = 15,
    ) -> None:
        super().__init__({}, interval_s)

        self._kube_client = kube_client
        self._api_client = api_client
        self._jobs_namespace = jobs_namespace
        self._job_label = job_label
        self._node_name = node_name

    async def get_latest_value(self) -> Mapping[str, Decimal]:
        # Calculate prices only for pods in Pending and Running phases
        pods = await self._kube_client.get_pods(
            namespace=self._jobs_namespace,
            label_selector=self._job_label,
            field_selector=",".join(
                (
                    f"spec.nodeName={self._node_name}",
                    "status.phase!=Failed",
                    "status.phase!=Succeeded",
                    "status.phase!=Unknown",
                ),
            ),
        )
        if not pods:
            logger.info("Node doesn't have any pods in Running phase")
            return {}
        result: dict[str, Decimal] = {}
        for pod in pods:
            pod_name = pod.metadata.name
            logger.debug("Checking pod %r credits per hour", pod_name)
            try:
                job = await self._api_client.jobs.status(pod_name)
            except ResourceNotFound:
                logger.warning("Job %r not found", pod_name)
                continue
            if not self._should_collect(job):
                logger.debug("Pod %r credits won't be collected", pod_name)
                continue
            logger.debug("Pod %r credits: %s", pod_name, job.total_price_credits)
            result[pod_name] = job.total_price_credits
        return result

    def _should_collect(self, job: JobDescription) -> bool:
        finished_at = job.history.finished_at
        if finished_at is None:
            return True
        # Pod credits should not be collected after job finished.
        # Ensure metric is exported during two intervals after job finished
        # to allow Prometheus to collect it.
        return datetime.now(timezone.utc) - finished_at <= timedelta(
            seconds=2 * self._interval_s
        )


class NodeCPUPowerCollector(Collector[CPUPowerConsuption]):
    def __init__(
        self,
        config_client: ConfigClient,
        cluster_name: str,
        node_pool_name: str,
        interval_s: float = 3600,
    ) -> None:
        super().__init__(CPUPowerConsuption(), interval_s)

        self._config_client = config_client
        self._cluster_name = cluster_name
        self._node_pool_name = node_pool_name

    async def get_latest_value(self) -> CPUPowerConsuption:
        cluster = await self._config_client.get_cluster(self._cluster_name)
        assert cluster.cloud_provider is not None
        for node_pool in cluster.cloud_provider.node_pools:
            if node_pool.name == self._node_pool_name:
                return CPUPowerConsuption(
                    min_consumption=node_pool.cpu_min_watts,
                    max_consumption=node_pool.cpu_max_watts,
                    co2_grams_eq_per_kwh=node_pool.co2_grams_eq_per_kwh,
                )
        logger.warning(
            f"Node pool '{self._node_pool_name}' was not found "
            f"in cluster '{self._cluster_name}."
        )
        return CPUPowerConsuption()

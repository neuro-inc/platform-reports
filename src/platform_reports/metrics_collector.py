from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, time, tzinfo
from decimal import Decimal
from importlib.resources import files
from pathlib import Path
from types import TracebackType
from typing import Any, Generic, Self, TypeVar

import aiohttp
from aiobotocore.client import AioBaseClient
from cachetools import TTLCache
from google.oauth2.service_account import Credentials
from googleapiclient import discovery
from neuro_config_client import Cluster
from neuro_logging import new_trace_cm, trace_cm
from yarl import URL

from .cluster import ClusterHolder
from .config import Label
from .kube_client import KubeClient, Node, Pod, PodCondition


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
class NodeEnergyConsumption:
    cpu_min_watts: float = 0
    cpu_max_watts: float = 0
    co2_grams_eq_per_kwh: float = 0
    price_per_kwh: Decimal = Decimal("0")


_TValue = TypeVar("_TValue")


def _get_node_region(node: Node) -> str:
    return (
        node.metadata.labels.get(Label.FAILURE_DOMAIN_REGION_KEY)
        or node.metadata.labels[Label.TOPOLOGY_REGION_KEY]
    )


def _get_node_zone(node: Node) -> str:
    return (
        node.metadata.labels.get(Label.FAILURE_DOMAIN_ZONE_KEY)
        or node.metadata.labels[Label.TOPOLOGY_ZONE_KEY]
    )


def _get_node_instance_type(node: Node) -> str:
    return (
        node.metadata.labels.get(Label.NODE_INSTANCE_TYPE_KEY)
        or node.metadata.labels[Label.INSTANCE_TYPE_KEY]
    )


def _is_preemptible_node(node: Node) -> bool:
    return Label.NEURO_PREEMPTIBLE_KEY in node.metadata.labels


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
        logger.debug("Updated value to %s", self._value)
        return self._update()

    async def _update(self) -> None:
        while True:
            try:
                logger.debug("Next update will be in %s seconds", self._interval_s)
                await asyncio.sleep(self._interval_s)

                async with new_trace_cm(
                    name=f"{self.__class__.__name__}.get_latest_value"
                ):
                    self._value = await self.get_latest_value()

                logger.debug("Updated value to %s", self._value)
            except asyncio.CancelledError:
                raise
            except Exception as ex:
                logger.warning(
                    "Unexpected error ocurred during value update", exc_info=ex
                )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        pass


class NodePriceCollector(Collector[Mapping[str, Price]]):
    def __init__(
        self,
        *,
        kube_client: KubeClient,
        initial_value: Mapping[str, Price],
        interval_s: float = 15,
    ) -> None:
        super().__init__(initial_value, interval_s)

        self._kube_client = kube_client

    async def get_latest_value(self) -> Mapping[str, Price]:
        nodes = await self._kube_client.get_nodes(
            label_selector=Label.NEURO_NODE_POOL_KEY
        )
        if not nodes:
            return {}

        prices = {}

        for node in nodes:
            node_up_time = datetime.now(UTC) - node.metadata.creation_timestamp
            price_per_hour = await self.get_price_per_hour(node)
            prices[node.metadata.name] = Price(
                currency=price_per_hour.currency,
                value=round(
                    price_per_hour.value * int(node_up_time.total_seconds()) / 3600, 2
                ),
            )

        return prices

    async def get_price_per_hour(self, node: Node) -> Price:
        return Price()


class ConfigPriceCollector(NodePriceCollector):
    def __init__(
        self,
        *,
        kube_client: KubeClient,
        cluster_holder: ClusterHolder,
        interval_s: float = 60,
    ) -> None:
        super().__init__(
            kube_client=kube_client, initial_value={}, interval_s=interval_s
        )

        self._cluster_holder = cluster_holder
        self._client: Any = None

    async def get_price_per_hour(self, node: Node) -> Price:
        cluster = self._cluster_holder.cluster
        assert cluster.orchestrator is not None

        node_pool_name = node.metadata.labels[Label.NEURO_NODE_POOL_KEY]
        for resource_pool in cluster.orchestrator.resource_pool_types:
            if resource_pool.name == node_pool_name:
                return Price(
                    value=resource_pool.price, currency=resource_pool.currency or ""
                )

        logger.warning("Node pool %s was not found in cluster", node_pool_name)

        return Price()


class AWSNodePriceCollector(NodePriceCollector):
    def __init__(
        self,
        *,
        kube_client: KubeClient,
        pricing_client: AioBaseClient,
        ec2_client: AioBaseClient,
        interval_s: float = 15,
    ) -> None:
        super().__init__(
            kube_client=kube_client, initial_value={}, interval_s=interval_s
        )

        self._pricing_client = pricing_client
        self._ec2_client = ec2_client
        self._region_long_names = self._get_region_long_names()
        self._on_demand_price_cache: TTLCache[tuple[str, str], Price] = TTLCache(
            maxsize=1000, ttl=3600
        )
        self._spot_price_cache: TTLCache[tuple[str, str], Price] = TTLCache(
            maxsize=1000, ttl=3600
        )

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

    async def get_price_per_hour(self, node: Node) -> Price:
        region = _get_node_region(node)
        zone = _get_node_zone(node)
        instance_type = _get_node_instance_type(node)
        is_spot = _is_preemptible_node(node)
        if is_spot:
            return await self._get_latest_spot_price(
                zone=zone, instance_type=instance_type
            )
        return await self._get_latest_on_demand_price(
            region_long_name=self._region_long_names[region],
            instance_type=instance_type,
        )

    async def _get_latest_on_demand_price(
        self, *, region_long_name: str, instance_type: str
    ) -> Price:
        cache_key = (region_long_name, instance_type)
        if price := self._on_demand_price_cache.get(cache_key):
            return price
        price = await self._get_latest_on_demand_price_from_api(
            region_long_name=region_long_name, instance_type=instance_type
        )
        self._on_demand_price_cache[cache_key] = price
        return price

    async def _get_latest_on_demand_price_from_api(
        self, *, region_long_name: str, instance_type: str
    ) -> Price:
        response = await self._pricing_client.get_products(
            ServiceCode="AmazonEC2",
            FormatVersion="aws_v1",
            Filters=[
                self._create_filter("ServiceCode", "AmazonEC2"),
                self._create_filter("locationType", "AWS Region"),
                self._create_filter("location", region_long_name),
                self._create_filter("operatingSystem", "Linux"),
                self._create_filter("tenancy", "Shared"),
                self._create_filter("capacitystatus", "Used"),
                self._create_filter("preInstalledSw", "NA"),
                self._create_filter("instanceType", instance_type),
            ],
        )
        if len(response["PriceList"]) != 1:
            logger.warning(
                "AWS returned %d products in %s region, cannot determine node price",
                len(response["PriceList"]),
                region_long_name,
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

    async def _get_latest_spot_price(self, zone: str, instance_type: str) -> Price:
        cache_key = (zone, instance_type)
        if price := self._spot_price_cache.get(cache_key):
            return price
        price = await self._get_latest_spot_price_from_api(
            zone=zone, instance_type=instance_type
        )
        self._spot_price_cache[cache_key] = price
        return price

    async def _get_latest_spot_price_from_api(
        self, zone: str, instance_type: str
    ) -> Price:
        response = await self._ec2_client.describe_spot_price_history(
            AvailabilityZone=zone,
            InstanceTypes=[instance_type],
            ProductDescriptions=["Linux/UNIX"],
            StartTime=datetime.now(UTC).replace(tzinfo=None),
        )
        history = response["SpotPriceHistory"]
        if len(history) == 0:
            logger.warning(
                "AWS didn't return spot price history for %s instance in %s zone",
                instance_type,
                zone,
            )
            return Price()
        return Price(currency="USD", value=Decimal(str(history[0]["SpotPrice"])))


class AzureNodePriceCollector(NodePriceCollector):
    def __init__(
        self,
        *,
        kube_client: KubeClient,
        prices_client: aiohttp.ClientSession,
        prices_url: URL,
        interval_s: float = 15,
    ) -> None:
        super().__init__(
            kube_client=kube_client, initial_value={}, interval_s=interval_s
        )

        self._prices_client = prices_client
        self._prices_url = prices_url
        self._price_cache: TTLCache[tuple[str, str, str], Price] = TTLCache(
            maxsize=1000, ttl=3600
        )

    async def get_price_per_hour(self, node: Node) -> Price:
        region = _get_node_region(node)
        instance_type = _get_node_instance_type(node)
        is_spot = _is_preemptible_node(node)
        base_instance_type = self._get_base_instance_type(instance_type)
        sku_name = self._get_sku_name(instance_type=base_instance_type, is_spot=is_spot)

        cache_key = (region, base_instance_type, sku_name)
        if price := self._price_cache.get(cache_key):
            return price
        price = await self._get_price_per_hour(
            region=region, base_instance_type=base_instance_type, sku_name=sku_name
        )
        self._price_cache[cache_key] = price
        return price

    async def _get_price_per_hour(
        self, *, region: str, base_instance_type: str, sku_name: str
    ) -> Price:
        payload = await self._get_prices_from_api(
            region=region, instance_type=base_instance_type, sku_name=sku_name
        )
        items = [
            i for i in payload["Items"] if "windows" not in i["productName"].lower()
        ]
        if len(items) != 1:
            logger.warning(
                "Azure returned %d products in %s region, cannot determine node price",
                len(items),
                region,
            )
            return Price()
        return Price(
            currency=items[0]["currencyCode"],
            value=Decimal(str(items[0]["retailPrice"])),
        )

    def _get_sku_name(self, *, instance_type: str, is_spot: bool) -> str:
        sku_name = " ".join(instance_type.split("_")[1:])
        if is_spot:
            sku_name += " Spot"
        return sku_name

    def _get_base_instance_type(self, instance_type: str) -> str:
        # For on-demand instances base instance price should be taken.
        # e. g.: Standard_D2_v3 instead of Standard_D2s_v3
        match = re.search(r"^([^_]+)_(D\d\d?)a?s?_(v\d)$", instance_type)
        if match:
            instance_type = f"{match.group(1)}_{match.group(2)}_{match.group(3)}"
        return instance_type

    async def _get_prices_from_api(
        self, *, region: str, instance_type: str, sku_name: str
    ) -> dict[str, Any]:
        response = await self._prices_client.get(
            (self._prices_url / "api/retail/prices").with_query(
                {
                    "$filter": " ".join(
                        [
                            "serviceName eq 'Virtual Machines'",
                            "and priceType eq 'Consumption'",
                            f"and armRegionName eq '{region}'",
                            f"and armSkuName eq '{instance_type}'",
                            f"and skuName eq '{sku_name}'",
                        ]
                    )
                }
            )
        )
        response.raise_for_status()
        return await response.json()


class GCPNodePriceCollector(NodePriceCollector):
    def __init__(
        self,
        *,
        kube_client: KubeClient,
        cluster_holder: ClusterHolder,
        service_account_path: Path,
        interval_s: float = 15,
    ) -> None:
        super().__init__(
            kube_client=kube_client, initial_value={}, interval_s=interval_s
        )

        self._cluster_holder = cluster_holder
        self._service_account_path = service_account_path
        self._loop = asyncio.get_event_loop()
        self._client: Any = None
        self._skus_cache: TTLCache[str, list[dict[str, Any]]] = TTLCache(
            maxsize=1000, ttl=3600
        )

    async def __aenter__(self) -> Self:
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

    def _get_usage_type(self, is_preemptible: bool) -> str:  # noqa: FBT001
        return "preemptible" if is_preemptible else "ondemand"

    async def get_price_per_hour(self, node: Node) -> Price:
        region = _get_node_region(node)
        instance_type = _get_node_instance_type(node)
        instance_family = self._get_instance_family(instance_type)
        is_preemptive = _is_preemptible_node(node)
        usage_type = self._get_usage_type(is_preemptive)
        node_pool_name = node.metadata.labels[Label.NEURO_NODE_POOL_KEY]

        cluster = self._cluster_holder.cluster
        assert cluster.orchestrator is not None
        resource_pools = {r.name: r for r in cluster.orchestrator.resource_pool_types}
        if node_pool_name not in resource_pools:
            return Price(currency="USD")
        resource_pool = resource_pools[node_pool_name]
        return await self._get_instance_price_per_hour(
            region=region,
            instance_family=instance_family,
            usage_type=usage_type,
            cpu=resource_pool.cpu,
            memory=resource_pool.memory,
            gpu=resource_pool.nvidia_gpu or 0,
            gpu_model=resource_pool.nvidia_gpu_model or "",
        )

    async def _get_instance_price_per_hour(
        self,
        *,
        region: str,
        instance_family: str,
        usage_type: str,
        cpu: float,
        memory: int,
        gpu: int,
        gpu_model: str,
    ) -> Price:
        prices_in_nanos: dict[str, Decimal] = {}
        expected_prices_count = bool(cpu) + bool(memory) + bool(gpu)
        gpu_model = gpu_model.replace("-", " ").lower()
        service_skus = await self._get_service_skus(region)
        for sku in service_skus:
            # The only reliable way to match instance type with sku is through
            # sku description field. Other sku fields don't contain instance type
            # specific fields.
            sku_description = sku["description"].lower()
            sku_description_words = set(sku_description.split())
            sku_usage_type = sku["category"]["usageType"].lower()

            # Calculate price for CPU and RAM
            if (
                instance_family in sku_description_words
                and usage_type == sku_usage_type
                and not sku_description_words.intersection(
                    ("sole", "tenancy", "custom")
                )
            ):
                price_in_nanos = self._get_price_in_nanos(sku)
                if sku_description_words.intersection(("core", "cpu", "vcpu")):
                    assert "cpu" not in prices_in_nanos
                    prices_in_nanos["cpu"] = price_in_nanos * Decimal(str(cpu))
                if "ram" in sku_description_words:
                    assert "ram" not in prices_in_nanos
                    prices_in_nanos["ram"] = price_in_nanos * memory / 1024**3

            # Calculate price for the attached GPU
            if gpu and gpu_model in sku_description and usage_type == sku_usage_type:
                price_in_nanos = self._get_price_in_nanos(sku)
                assert "gpu" not in prices_in_nanos
                prices_in_nanos["gpu"] = gpu * price_in_nanos

            if len(prices_in_nanos) == expected_prices_count:
                break
        assert len(prices_in_nanos) == expected_prices_count, (
            f"Found prices only for: [{', '.join(prices_in_nanos.keys()).upper()}]"
        )
        return Price(
            value=sum(prices_in_nanos.values(), Decimal()) / 10**9, currency="USD"
        )

    async def _get_service_skus(self, region: str) -> list[dict[str, Any]]:
        if skus := self._skus_cache.get(region):
            return skus
        skus = await self._get_service_skus_from_api(region)
        self._skus_cache[region] = skus
        return skus

    async def _get_service_skus_from_api(self, region: str) -> list[dict[str, Any]]:
        skus = []
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
                    and region in sku["serviceRegions"]
                ):
                    skus.append(sku)
            next_page_token = response.get("nextPageToken") or None
        return skus

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
        *,
        kube_client: KubeClient,
        cluster_holder: ClusterHolder,
        interval_s: float = 15,
    ) -> None:
        super().__init__({}, interval_s)

        self._kube_client = kube_client
        self._cluster_holder = cluster_holder

    async def get_latest_value(self) -> Mapping[str, Decimal]:
        cluster = self._cluster_holder.cluster
        if cluster.orchestrator is None:
            logger.warning("Cluster doesn't have orchestrator config")
            return {}
        presets = {
            preset.name: preset for preset in cluster.orchestrator.resource_presets
        }
        if not presets:
            return {}
        pods = await self._kube_client.get_pods(
            label_selector=f"{Label.APOLO_ORG_KEY},{Label.APOLO_PROJECT_KEY}",
        )
        result: dict[str, Decimal] = {}
        for pod in pods:
            try:
                if not pod.status.is_scheduled:
                    logger.debug("Pod %r is not scheduled, skipping", pod.metadata.name)
                    continue
                pod_name = pod.metadata.name
                preset_name = pod.metadata.labels.get(
                    Label.APOLO_PRESET_KEY
                ) or pod.metadata.labels.get(Label.NEURO_PRESET_KEY)
                if not preset_name:
                    logger.warning(
                        "Pod %r has no preset label, skipping", pod_name, preset_name
                    )
                    continue
                if not (preset := presets.get(preset_name)):
                    logger.warning(
                        "Pod %r resource preset %r not found, skipping",
                        pod_name,
                        preset_name,
                    )
                    continue
                credits_total = self._get_pod_credits_total(
                    pod, preset.credits_per_hour
                )
                logger.debug("Pod %r credits total: %s", pod_name, credits_total)
                result[pod_name] = credits_total
            except Exception as ex:
                logger.exception(str(ex))
        return result

    @classmethod
    def _get_pod_credits_total(cls, pod: Pod, credits_per_hour: Decimal) -> Decimal:
        pod_scheduled = pod.status.get_condition(PodCondition.Type.POD_SCHEDULED)
        start_date = pod_scheduled.last_transition_time
        if pod.status.is_terminated:
            run_time = pod.status.finish_date - start_date
        else:
            run_time = datetime.now(UTC) - start_date
        credits_total = Decimal(run_time.total_seconds()) * credits_per_hour / 3600
        return round(credits_total, 2)


class NodeEnergyConsumptionCollector(Collector[Mapping[str, NodeEnergyConsumption]]):
    def __init__(
        self,
        *,
        kube_client: KubeClient,
        cluster_holder: ClusterHolder,
        interval_s: float = 15,
        current_time_factory: Callable[[tzinfo], datetime] = datetime.now,
    ) -> None:
        super().__init__({}, interval_s)

        self._kube_client = kube_client
        self._cluster_holder = cluster_holder
        self._current_time_factory = current_time_factory

    def _get_price_per_kwh(self, cluster: Cluster) -> Decimal:
        if cluster.energy is None:
            return Decimal(0)
        default_schedule = next(
            s for s in cluster.energy.schedules if s.name == "default"
        )
        custom_schedules = [s for s in cluster.energy.schedules if s.name != "default"]
        now = self._current_time_factory(cluster.timezone)
        now_weekday = now.weekday() + 1
        now_time = now.timetz()
        now_time = time(
            hour=now_time.hour, minute=now_time.minute, tzinfo=now_time.tzinfo
        )
        for schedule in custom_schedules:
            for period in schedule.periods:
                if (
                    period.weekday == now_weekday
                    and period.start_time <= now_time <= period.end_time
                ):
                    return schedule.price_per_kwh
        return default_schedule.price_per_kwh

    def _get_co2_grams_eq_per_kwh(self, cluster: Cluster) -> float:
        return cluster.energy.co2_grams_eq_per_kwh if cluster.energy else 0

    async def get_latest_value(self) -> Mapping[str, NodeEnergyConsumption]:
        nodes = await self._kube_client.get_nodes(
            label_selector=Label.NEURO_NODE_POOL_KEY
        )
        if not nodes:
            return {}

        cluster = self._cluster_holder.cluster
        assert cluster.cloud_provider is not None

        co2_grams_eq_per_kwh = self._get_co2_grams_eq_per_kwh(cluster)
        price_per_kwh = self._get_price_per_kwh(cluster)
        energy_consumptions = {}

        for node in nodes:
            node_pool_name = node.metadata.labels[Label.NEURO_NODE_POOL_KEY]
            energy_consumption = NodeEnergyConsumption(
                co2_grams_eq_per_kwh=co2_grams_eq_per_kwh,
                price_per_kwh=price_per_kwh,
            )
            for node_pool in cluster.cloud_provider.node_pools:
                if node_pool.name == node_pool_name:
                    energy_consumption = replace(
                        energy_consumption,
                        cpu_min_watts=node_pool.cpu_min_watts,
                        cpu_max_watts=node_pool.cpu_max_watts,
                    )
                    break
            else:
                logger.warning("Node pool %s was not found in cluster", node_pool_name)
            energy_consumptions[node.metadata.name] = energy_consumption

        return energy_consumptions

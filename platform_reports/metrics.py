import asyncio
import json
import logging
from dataclasses import dataclass
from importlib.resources import path
from pathlib import Path
from types import TracebackType
from typing import (
    Any,
    Awaitable,
    Dict,
    Generic,
    Iterator,
    Mapping,
    Optional,
    Type,
    TypeVar,
)

from aiobotocore.client import AioBaseClient
from google.oauth2.service_account import Credentials
from googleapiclient import discovery
from platform_config_client import Cluster, ConfigClient

from .kube_client import KubeClient, Pod, Resources


logger = logging.getLogger(__name__)


GOOGLE_COMPUTE_ENGINE_ID = "services/6F81-5844-456A"


@dataclass(frozen=True)
class Price:
    currency: str = ""
    value: float = 0.0

    def __str__(self) -> str:
        return f"{self.value} ({self.currency})" if self.currency else str(self.value)

    def __repr__(self) -> str:
        return self.__str__()


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
                self._value = await self.get_latest_value()
                logger.info("Updated value to %s", self._value)
            except asyncio.CancelledError:
                raise
            except Exception as ex:
                logger.warning(
                    "Unexpected error ocurred during value update", exc_info=ex
                )

    async def __aenter__(self) -> "Collector[_TValue]":
        return self

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        pass


class AWSNodePriceCollector(Collector[Price]):
    def __init__(
        self,
        pricing_client: AioBaseClient,
        region: str,
        instance_type: str,
        interval_s: float = 3600,
    ) -> None:
        super().__init__(Price(), interval_s)
        self._pricing_client = pricing_client
        self._region = region
        self._region_long_name = ""
        self._instance_type = instance_type

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
    def _get_region_long_names(self) -> Dict[str, str]:
        result: Dict[str, str] = {}
        # https://github.com/boto/botocore/blob/master/botocore/data/endpoints.json
        with path("botocore", "data") as data_path:
            endpoints_path = data_path / "endpoints.json"
            with endpoints_path.open("r", encoding="utf-8") as f:
                endpoints = json.load(f)
        for partition in endpoints["partitions"]:
            regions = partition["regions"]
            for region in regions:
                result[region] = regions[region]["description"]
        # The Osaka region is special and is not on the list of endpoints in botocore
        result["ap-northeast-3"] = "Asia Pacific (Osaka-Local)"
        return result

    async def get_latest_value(self) -> Price:
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
            return Price(currency="USD", value=float(price_per_unit["USD"]))
        logger.warning(
            "AWS product currencies are not supported: %s", list(price_per_unit.keys())
        )
        return Price()

    def _create_filter(self, field: str, value: str) -> Dict[str, str]:
        return {"Type": "TERM_MATCH", "Field": field, "Value": value}


class AzureNodePriceCollector(Collector[Price]):
    def __init__(self, instance_type: str, interval_s: float = 3600) -> None:
        super().__init__(Price(), interval_s)

        self._instance_type = instance_type
        self._instance_prices = {
            k.lower(): v for k, v in self.get_instance_prices().items()
        }

    @classmethod
    def get_instance_prices(cls) -> Dict[str, Price]:
        # TODO: Load instance prices from Azure API.
        # Currently Azure does not expose instance prices in their API.
        return {
            # General Purpose
            "Standard_D2s_v3": Price(value=0.096, currency="USD"),
            "Standard_D4s_v3": Price(value=0.192, currency="USD"),
            "Standard_D8s_v3": Price(value=0.384, currency="USD"),
            "Standard_D16s_v3": Price(value=0.768, currency="USD"),
            "Standard_D32s_v3": Price(value=1.536, currency="USD"),
            "Standard_D48s_v3": Price(value=2.304, currency="USD"),
            "Standard_D64s_v3": Price(value=3.072, currency="USD"),
            # Nvidia Tesla K80
            "Standard_NC6": Price(value=0.9, currency="USD"),
            "Standard_NC12": Price(value=1.8, currency="USD"),
            "Standard_NC24": Price(value=3.6, currency="USD"),
            "Standard_NC24r": Price(value=3.96, currency="USD"),
            # Nvidia Tesla P100
            "Standard_NC6s_v2": Price(value=2.07, currency="USD"),
            "Standard_NC12s_v2": Price(value=4.14, currency="USD"),
            "Standard_NC24s_v2": Price(value=8.28, currency="USD"),
            "Standard_NC24rs_v2": Price(value=9.108, currency="USD"),
            # Nvidia Tesla V100
            "Standard_NC6s_v3": Price(value=3.06, currency="USD"),
            "Standard_NC12s_v3": Price(value=6.12, currency="USD"),
            "Standard_NC24s_v3": Price(value=12.24, currency="USD"),
            "Standard_NC24rs_v3": Price(value=13.464, currency="USD"),
            # Nvidia Tesla M60
            "Standard_NV12s_v3": Price(value=1.14, currency="USD"),
            "Standard_NV24s_v3": Price(value=2.28, currency="USD"),
            "Standard_NV48s_v3": Price(value=4.56, currency="USD"),
            # Nvidia Tesla P40
            "Standard_ND6s": Price(value=2.07, currency="USD"),
            "Standard_ND12s": Price(value=4.14, currency="USD"),
            "Standard_ND24s": Price(value=8.28, currency="USD"),
            "Standard_ND24rs": Price(value=9.108, currency="USD"),
            # 8 x Nvidia Tesla V100
            "Standard_ND40rs_v2": Price(value=22.032, currency="USD"),
        }

    async def get_latest_value(self) -> Price:
        instance_type = self._instance_type.lower()
        assert (
            instance_type in self._instance_prices
        ), f"Instance type {self._instance_type} has no registered price"
        return self._instance_prices[instance_type]


class GCPNodePriceCollector(Collector[Price]):
    def __init__(
        self,
        config_client: ConfigClient,
        service_account_path: Path,
        cluster_name: str,
        node_pool_name: str,
        region: str,
        instance_type: str,
        is_preemptible: bool,
        interval_s: float = 3600,
    ) -> None:
        super().__init__(Price(), interval_s)

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

    async def get_latest_value(self) -> Price:
        cluster = await self._config_client.get_cluster(self._cluster_name)
        resource_pools = {r.name: r for r in cluster.orchestrator.resource_pool_types}
        if self._node_pool_name not in resource_pools:
            return Price(currency="USD")
        resource_pool = resource_pools[self._node_pool_name]
        return await self._loop.run_in_executor(
            None,
            self._get_instance_price_per_hour,
            resource_pool.cpu,
            resource_pool.memory_mb / 1024,
            resource_pool.gpu or 0,
            resource_pool.gpu_model or "",
        )

    def _get_instance_price_per_hour(
        self, cpu: float, memory_gb: float, gpu: int, gpu_model: str
    ) -> Price:
        prices_in_nanos: Dict[str, float] = {}
        expected_prices_count = bool(cpu) + bool(memory_gb) + bool(gpu)
        gpu_model = gpu_model.replace("-", " ").lower()
        for sku in self._get_service_skus():
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
                    prices_in_nanos["cpu"] = cpu * price_in_nanos
                if "ram" in sku_description_words:
                    assert "ram" not in prices_in_nanos
                    prices_in_nanos["ram"] = memory_gb * price_in_nanos

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
        return Price(value=sum(prices_in_nanos.values(), 0.0) / 10 ** 9, currency="USD")

    def _get_service_skus(self) -> Iterator[Dict[str, Any]]:
        next_page_token: Optional[str] = ""
        while next_page_token is not None:
            request = (
                self._client.services()
                .skus()
                .list(
                    parent=GOOGLE_COMPUTE_ENGINE_ID,
                    currencyCode="USD",
                    pageToken=next_page_token,
                )
            )
            response = request.execute()
            for sku in response["skus"]:
                if (
                    sku["category"]["resourceFamily"] == "Compute"
                    and sku["category"]["usageType"] in ("OnDemand", "Preemptible")
                    and self._region in sku["serviceRegions"]
                ):
                    yield sku
            next_page_token = response.get("nextPageToken") or None

    def _get_price_in_nanos(self, sku: Dict[str, Any]) -> int:
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
        return int(unit_price["units"]) * 10 ** 9 + unit_price["nanos"]


class PodPriceCollector(Collector[Mapping[str, Price]]):
    def __init__(
        self,
        config_client: ConfigClient,
        kube_client: KubeClient,
        node_price_collector: Collector[Price],
        cluster_name: str,
        node_name: str,
        node_pool_name: str,
        jobs_namespace: str,
        job_label: str,
        interval_s: float = 60,
    ) -> None:
        super().__init__({}, interval_s)

        self._kube_client = kube_client
        self._config_client = config_client
        self._node_price_collector = node_price_collector
        self._jobs_namespace = jobs_namespace
        self._job_label = job_label
        self._cluster_name = cluster_name
        self._node_pool_name = node_pool_name
        self._node_name = node_name

    async def get_latest_value(self) -> Mapping[str, Price]:
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
        cluster = await self._config_client.get_cluster(self._cluster_name)
        node_resources = self._get_node_resources(cluster)
        logger.debug("Node resources: %s", node_resources)
        result: Dict[str, Price] = {}
        for pod in pods:
            pod_resources = self._get_pod_resources(pod)
            logger.debug("Pod %s resources: %s", pod.metadata.name, pod_resources)
            fraction = self._get_pod_resources_fraction(
                node_resources=node_resources, pod_resources=pod_resources
            )
            logger.debug("Pod %s fraction: %s", pod.metadata.name, fraction)
            node_price_per_hour = self._node_price_collector.current_value
            pod_price_per_hour = node_price_per_hour.value * fraction
            logger.debug(
                "Pod %s price per hour: %s", pod.metadata.name, pod_price_per_hour
            )
            result[pod.metadata.name] = Price(
                currency=node_price_per_hour.currency,
                value=pod_price_per_hour,
            )
        return result

    def _get_node_resources(self, cluster: Cluster) -> Resources:
        resource_pools = {r.name: r for r in cluster.orchestrator.resource_pool_types}
        if self._node_pool_name not in resource_pools:
            return Resources()
        resource_pool = resource_pools[self._node_pool_name]
        return Resources(
            cpu_m=int(resource_pool.cpu * 1000),
            memory_mb=resource_pool.memory_mb,
            gpu=resource_pool.gpu,
        )

    def _get_pod_resources(self, pod: Pod) -> Resources:
        cpu_m = 0
        memory_mb = 0
        gpu = 0
        for container in pod.containers:
            cpu_m += container.resource_requests.cpu_m
            memory_mb += container.resource_requests.memory_mb
            gpu += container.resource_requests.gpu
        return Resources(cpu_m=cpu_m, memory_mb=memory_mb, gpu=gpu)

    def _get_pod_resources_fraction(
        self, node_resources: Resources, pod_resources: Resources
    ) -> float:
        gpu_fraction = 0.0
        if node_resources.gpu and pod_resources.gpu:
            gpu_fraction = pod_resources.gpu / node_resources.gpu
        max_fraction = max(
            pod_resources.cpu_m / node_resources.cpu_m,
            pod_resources.memory_mb / node_resources.memory_mb,
            gpu_fraction,
        )
        return min(1.0, max_fraction)

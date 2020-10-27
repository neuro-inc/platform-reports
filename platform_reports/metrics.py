import asyncio
import json
import logging
from dataclasses import dataclass
from importlib.resources import path
from types import TracebackType
from typing import Awaitable, Dict, Generic, Mapping, Optional, Type, TypeVar

from aiobotocore.client import AioBaseClient
from platform_config_client import Cluster, ConfigClient

from .kube_client import KubeClient, Pod, Resources


logger = logging.getLogger(__name__)


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
                logger.info("Updated value to %s per hour", self._value)
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


@dataclass(frozen=True)
class Price:
    currency: str = ""
    value: float = 0.0

    def __str__(self) -> str:
        return f"{self.value} ({self.currency})" if self.currency else str(self.value)

    def __repr__(self) -> str:
        return self.__str__()


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
    pass


class GCPNodePriceCollector(Collector[Price]):
    pass


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
        pods = await self._kube_client.get_pods(
            namespace=self._jobs_namespace,
            label_selector=self._job_label,
            field_selector=",".join(
                (
                    f"spec.nodeName={self._node_name}",
                    "status.phase!=Failed",
                    "status.phase!=Succeeded",
                ),
            ),
        )
        if not pods:
            return {}
        cluster = await self._config_client.get_cluster(self._cluster_name)
        node_resources = self._get_node_resources(cluster)
        result: Dict[str, Price] = {}
        for pod in pods:
            pod_resources = self._get_pod_resources(pod)
            fraction = self._get_pod_resources_fraction(
                node_resources=node_resources, pod_resources=pod_resources
            )
            node_price_per_hour = self._node_price_collector.current_value
            result[pod.metadata.name] = Price(
                currency=node_price_per_hour.currency,
                value=node_price_per_hour.value * fraction,
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

import asyncio
import json
import logging
from dataclasses import dataclass
from types import TracebackType
from typing import Awaitable, Dict, Optional, Type

from aiobotocore.client import AioBaseClient
from pkg_resources import resource_filename


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Price:
    currency: str = ""
    value: float = 0.0

    def __str__(self) -> str:
        return f"{self.value} ({self.currency})" if self.currency else str(self.value)

    def __repr__(self) -> str:
        return self.__str__()


class PriceCollector:
    def __init__(self, interval_s: float = 3600) -> None:
        self._interval_s = interval_s
        self._price_per_hour = Price()

    @property
    def current_price_per_hour(self) -> Price:
        return self._price_per_hour

    async def get_latest_price_per_hour(self) -> Price:
        return Price()

    async def start(self) -> Awaitable[None]:
        self._price_per_hour = await self.get_latest_price_per_hour()
        logger.info("Updated node price to %s per hour", self._price_per_hour)
        return self._update()

    async def _update(self) -> None:
        while True:
            try:
                logger.info(
                    "Next node price update will be in %s seconds", self._interval_s
                )
                await asyncio.sleep(self._interval_s)
                self._price_per_hour = await self.get_latest_price_per_hour()
                logger.info("Updated price to %s per hour", self._price_per_hour)
            except asyncio.CancelledError:
                raise
            except Exception as ex:
                logger.warning(
                    "Unexpected error ocurred during price update", exc_info=ex
                )

    async def __aenter__(self) -> "PriceCollector":
        return self

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        pass


class AWSNodePriceCollector(PriceCollector):
    def __init__(
        self,
        pricing_client: AioBaseClient,
        region: str,
        instance_type: str,
        interval_s: float = 3600,
    ) -> None:
        super().__init__(interval_s)
        self._pricing_client = pricing_client
        self._region = region
        self._region_long_name = ""
        self._instance_type = instance_type

    async def __aenter__(self) -> PriceCollector:
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
        endpoints_file = resource_filename("botocore", "data/endpoints.json")
        with open(endpoints_file, "r") as f:
            endpoints = json.load(f)
        for partition in endpoints["partitions"]:
            regions = partition["regions"]
            for region in regions:
                result[region] = regions[region]["description"]
        # The Osaka region is special and is not on the list of endpoints in botocore
        result["ap-northeast-3"] = "Asia Pacific (Osaka-Local)"
        return result

    async def get_latest_price_per_hour(self) -> Price:
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


class AzureNodePriceCollector(PriceCollector):
    pass


class GCPNodePriceCollector(PriceCollector):
    pass

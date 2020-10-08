import asyncio
import logging
from dataclasses import dataclass
from types import TracebackType
from typing import Awaitable, Optional, Type


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
    pass


class AzureNodePriceCollector(PriceCollector):
    pass


class GCPNodePriceCollector(PriceCollector):
    pass

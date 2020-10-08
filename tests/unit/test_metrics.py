import asyncio
from contextlib import suppress
from typing import Iterator
from unittest import mock

import pytest

from platform_reports.metrics import Price, PriceCollector


class TestPriceCollector:
    @pytest.fixture
    def price_collector(self) -> PriceCollector:
        return PriceCollector(interval_s=0.1)

    @pytest.fixture
    def price_factory(self, price_collector: PriceCollector) -> Iterator[mock.Mock]:
        price = Price(currency="USD", value=1)
        with mock.patch.object(
            price_collector, "get_latest_price_per_hour", return_value=price,
        ) as mock_method:
            yield mock_method

    async def test_update(
        self, price_collector: PriceCollector, price_factory: mock.Mock
    ) -> None:
        task = asyncio.create_task(await price_collector.start())

        await asyncio.sleep(0.3)

        assert price_collector.current_price_per_hour == Price(currency="USD", value=1)
        assert price_factory.call_count >= 3

        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

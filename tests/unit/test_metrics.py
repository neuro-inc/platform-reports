import asyncio
import json
from contextlib import suppress
from typing import Iterator
from unittest import mock

import pytest
from aiobotocore.client import AioBaseClient

from platform_reports.metrics import AWSNodePriceCollector, Price, PriceCollector


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


class TestAWSNodePriceCollector:
    @pytest.fixture
    def ssm_client(self) -> mock.AsyncMock:
        result = mock.AsyncMock()
        result.get_parameter.return_value = {
            "Parameter": {"Value": "US East (N. Virginia)"}
        }
        return result

    @pytest.fixture
    def pricing_client(self) -> mock.AsyncMock:
        return mock.AsyncMock()

    @pytest.fixture
    def price_collector(
        self, ssm_client: AioBaseClient, pricing_client: AioBaseClient
    ) -> AWSNodePriceCollector:
        return AWSNodePriceCollector(
            ssm_client=ssm_client,
            pricing_client=pricing_client,
            region="us-east-1",
            instance_type="p2.xlarge",
            interval_s=0.1,
        )

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

        result = await price_collector.get_latest_price_per_hour()

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

        result = await price_collector.get_latest_price_per_hour()

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

        result = await price_collector.get_latest_price_per_hour()

        assert result == Price()

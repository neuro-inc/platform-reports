import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, Self, TypeVar

import aiohttp
import pydantic
from yarl import URL


LOGGER = logging.getLogger(__name__)


class PrometheusException(Exception):
    pass


class Metric(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(populate_by_name=True)

    @pydantic.dataclasses.dataclass(frozen=True)
    class Value:
        time: datetime
        value: Decimal

        @classmethod
        def validate(cls, value: Any, info: pydantic.ValidationInfo) -> Self:
            if isinstance(value, list | tuple):
                return cls(
                    time=datetime.fromtimestamp(value[0], UTC), value=Decimal(value[1])
                )
            return value

    labels: Mapping[str, str] = pydantic.Field(alias=str("metric"))  # type: ignore # noqa: UP018
    values: Sequence[Annotated[Value, pydantic.BeforeValidator(Value.validate)]]


TMetric = TypeVar("TMetric", bound=Metric)


class PrometheusClient:
    def __init__(
        self, *, client: aiohttp.ClientSession, prometheus_url: str | URL
    ) -> None:
        self._client = client
        self._prometheus_url = URL(prometheus_url)

    async def evaluate_range_query(
        self,
        *,
        query: str,
        start_date: datetime,
        end_date: datetime,
        step: float = 15,
        metric_cls: type[TMetric],
    ) -> list[TMetric]:
        url = self._prometheus_url / "api/v1/query_range"
        data = {
            "query": query,
            "start": start_date.timestamp(),
            "end": end_date.timestamp(),
            "step": step,
        }
        LOGGER.debug("Evaluating Prometheus range query: %s", data)
        async with self._client.post(url, data=data) as response:
            if response.status >= 400:
                response_text = await response.text()
                msg = f"Prometheus error: {response_text}"
                LOGGER.error(msg)
                raise PrometheusException(msg)
            response_json = await response.json()
        metrics = [
            metric_cls.model_validate(m)
            for m in response_json.get("data", {}).get("result", [])
        ]
        LOGGER.debug("Prometheus metrics: %s", metrics)
        return metrics

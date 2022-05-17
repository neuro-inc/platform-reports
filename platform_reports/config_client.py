from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Any

from neuro_config_client import Cluster, ConfigClient

logger = logging.getLogger(__name__)


class ClusterListener:
    def __init__(
        self, config_client: ConfigClient, cluster_name: str, *, interval_s: int = 5
    ) -> None:
        self._config_client = config_client
        self._cluster_name = cluster_name
        self._interval_s = interval_s
        self._task: asyncio.Task[None] | None = None
        self._cluster: Cluster | None = None

    async def __aenter__(self) -> ClusterListener:
        await self.start()
        return self

    async def __aexit__(self, *args: Any, **kwargs: Any) -> None:
        await self.stop()

    async def start(self) -> None:
        self._cluster = await self._config_client.get_cluster(self._cluster_name)
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        assert self._task
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        while True:
            try:
                self._cluster = await self._config_client.get_cluster(
                    self._cluster_name
                )
            except Exception as ex:
                logger.warning("Failed to get cluster", exc_info=ex)
            await asyncio.sleep(self._interval_s)

    @property
    def cluster(self) -> Cluster:
        assert self._cluster
        return self._cluster

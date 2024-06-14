from __future__ import annotations

import abc
import asyncio
import logging
from contextlib import suppress

from neuro_config_client import Cluster, ConfigClient

LOGGER = logging.getLogger(__name__)


class ClusterHolder(abc.ABC):
    @property
    @abc.abstractmethod
    def cluster(self) -> Cluster:
        pass


class RefreshableClusterHolder(ClusterHolder):
    def __init__(
        self,
        *,
        config_client: ConfigClient,
        cluster_name: str,
        update_cluster_interval: float = 15,
    ) -> None:
        self._config_client = config_client
        self._cluster_name = cluster_name
        self._update_cluster_interval = update_cluster_interval
        self._cluster: Cluster | None = None
        self._task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> ClusterHolder:
        self._cluster = await self._config_client.get_cluster(self._cluster_name)
        self._task = asyncio.create_task(
            self._run_cluster_updater(interval=self._update_cluster_interval)
        )
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._task:
            self._task.cancel()

            with suppress(asyncio.CancelledError):
                await self._task

    async def _run_cluster_updater(self, *, interval: float) -> None:
        while True:
            try:
                self._cluster = await self._config_client.get_cluster(
                    self._cluster_name
                )
            except Exception:
                LOGGER.exception("Failed to fetch cluster")
            await asyncio.sleep(interval)

    @property
    def cluster(self) -> Cluster:
        if self._cluster is None:
            raise ValueError("Cluster not found")
        return self._cluster

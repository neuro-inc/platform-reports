from __future__ import annotations

import asyncio
import enum
import logging
import ssl
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Any

import aiohttp
from dateutil.parser import parse
from neuro_logging import trace
from yarl import URL

from .config import KubeClientAuthType, KubeConfig

logger = logging.getLogger(__name__)


class KubeClientError(Exception):
    pass


class KubeClientUnauthorized(KubeClientError):
    pass


@dataclass(frozen=True)
class Metadata:
    name: str
    created_at: datetime
    labels: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> Metadata:
        return cls(
            name=payload["name"],
            created_at=parse(payload["creationTimestamp"]),
            labels=payload.get("labels", {}),
        )


@dataclass(frozen=True)
class Node:
    metadata: Metadata

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> Node:
        return cls(metadata=Metadata.from_payload(payload["metadata"]))


class PodPhase(str, enum.Enum):
    PENDING = "Pending"
    RUNNING = "Running"
    SUCCEEDED = "Succeeded"
    FAILED = "Failed"
    UNKNOWN = "Unknown"


@dataclass(frozen=True)
class PodStatus:
    phase: PodPhase

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> PodStatus:
        return cls(phase=PodPhase(payload.get("phase", "Unknown")))


@dataclass(frozen=True)
class Pod:
    metadata: Metadata
    status: PodStatus

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> Pod:
        return cls(
            metadata=Metadata.from_payload(payload["metadata"]),
            status=PodStatus.from_payload(payload.get("status", {})),
        )


class KubeClient:
    def __init__(
        self,
        config: KubeConfig,
        trace_configs: list[aiohttp.TraceConfig] | None = None,
    ) -> None:
        self._config = config
        self._token = config.token
        self._trace_configs = trace_configs
        self._client: aiohttp.ClientSession | None = None
        self._token_updater_task: asyncio.Task[None] | None = None

    def _create_ssl_context(self) -> ssl.SSLContext | None:
        if self._config.url.scheme != "https":
            return None
        ssl_context = ssl.create_default_context(
            cafile=self._config.cert_authority_path,
            cadata=self._config.cert_authority_data_pem,
        )
        if self._config.auth_type == KubeClientAuthType.CERTIFICATE:
            ssl_context.load_cert_chain(
                self._config.client_cert_path,  # type: ignore
                self._config.client_key_path,
            )
        return ssl_context

    async def __aenter__(self) -> KubeClient:
        await self._init()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def _init(self) -> None:
        connector = aiohttp.TCPConnector(
            limit=self._config.conn_pool_size, ssl=self._create_ssl_context()
        )
        if self._config.token_path:
            self._token = Path(self._config.token_path).read_text()
            self._token_updater_task = asyncio.create_task(self._start_token_updater())
        timeout = aiohttp.ClientTimeout(
            connect=self._config.conn_timeout_s, total=self._config.read_timeout_s
        )
        self._client = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            trace_configs=self._trace_configs,
        )

    async def _start_token_updater(self) -> None:
        if not self._config.token_path:
            return
        while True:
            try:
                token = Path(self._config.token_path).read_text()
                if token != self._token:
                    self._token = token
                    logger.info("Kube token was refreshed")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Failed to update kube token: %s", exc)
            await asyncio.sleep(self._config.token_update_interval_s)

    async def aclose(self) -> None:
        if self._client:
            await self._client.close()
        if self._token_updater_task:
            self._token_updater_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._token_updater_task
            self._token_updater_task = None

    def _get_pods_url(self, namespace: str) -> URL:
        if namespace:
            return self._config.url / "api/v1/namespaces" / namespace / "pods"
        return self._config.url / "api/v1/pods"

    @trace
    async def get_node(self, name: str) -> Node:
        url = self._config.url / "api/v1/nodes" / name
        payload = await self._request(method="get", url=url)
        assert payload["kind"] == "Node"
        return Node.from_payload(payload)

    @trace
    async def get_pods(
        self, namespace: str = "", field_selector: str = "", label_selector: str = ""
    ) -> Sequence[Pod]:
        params: dict[str, str] = {}
        if field_selector:
            params["fieldSelector"] = field_selector
        if label_selector:
            params["labelSelector"] = label_selector
        payload = await self._request(
            method="get", url=self._get_pods_url(namespace), params=params or None
        )
        assert payload["kind"] == "PodList"
        return [Pod.from_payload(i) for i in payload["items"]]

    def _create_headers(self, headers: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = dict(headers) if headers else {}
        if self._config.auth_type == KubeClientAuthType.TOKEN and self._token:
            headers["Authorization"] = "Bearer " + self._token
        return headers

    async def _request(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        headers = self._create_headers(kwargs.pop("headers", None))
        assert self._client, "client is not initialized"
        async with self._client.request(*args, headers=headers, **kwargs) as resp:
            payload = await resp.json()
            self._raise_for_status(payload)
            return payload

    def _raise_for_status(self, payload: dict[str, Any]) -> None:
        kind = payload["kind"]
        if kind == "Status":
            if payload.get("status") == "Success":
                return
            code = payload.get("code")
            if code == 401:
                raise KubeClientUnauthorized(payload)
            raise KubeClientError(payload["message"])

from __future__ import annotations

import enum
import logging
import ssl
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from math import ceil
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
class Resources:
    cpu_m: int = 0
    memory_mb: int = 0
    gpu: int = 0

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> Resources:
        return cls(
            cpu_m=cls._parse_cpu_m(payload.get("cpu", "0")),
            memory_mb=cls._parse_memory(payload.get("memory", "0Mi")),
            gpu=int(payload.get("nvidia.com/gpu", 0)),
        )

    @classmethod
    def _parse_cpu_m(cls, value: str) -> int:
        if value.endswith("m"):
            return int(value[:-1])
        return int(float(value) * 1000)

    @classmethod
    def _parse_memory(cls, memory: str) -> int:
        try:
            memory_b = int(memory)
        except ValueError:
            if memory.endswith("Ki"):
                memory_b = int(memory[:-2]) * 1024
            elif memory.endswith("K"):
                memory_b = int(memory[:-1]) * 1000
            elif memory.endswith("Mi"):
                return int(memory[:-2])
            elif memory.endswith("M"):
                memory_b = int(memory[:-1]) * 1000**2
            elif memory.endswith("Gi"):
                memory_b = int(memory[:-2]) * 1024**3
            elif memory.endswith("G"):
                memory_b = int(memory[:-1]) * 1000**3
            else:
                raise ValueError(f"{memory!r} memory format is not supported")
        return ceil(memory_b / 1024**2)


@dataclass(frozen=True)
class Container:
    name: str
    resource_requests: Resources = field(default_factory=Resources)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> Container:
        return cls(
            name=payload["name"],
            resource_requests=Resources.from_payload(
                payload.get("resources", {}).get("requests", {})
            ),
        )


@dataclass(frozen=True)
class Pod:
    metadata: Metadata
    status: PodStatus
    containers: Sequence[Container]

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> Pod:
        return cls(
            metadata=Metadata.from_payload(payload["metadata"]),
            status=PodStatus.from_payload(payload.get("status", {})),
            containers=[
                Container.from_payload(c) for c in payload["spec"]["containers"]
            ],
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
        self._client = await self._create_http_client()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def _create_http_client(self) -> aiohttp.ClientSession:
        connector = aiohttp.TCPConnector(
            limit=self._config.conn_pool_size, ssl=self._create_ssl_context()
        )
        if self._config.auth_type == KubeClientAuthType.TOKEN:
            token = self._token
            if not token:
                assert self._config.token_path is not None
                token = Path(self._config.token_path).read_text()
            headers = {"Authorization": "Bearer " + token}
        else:
            headers = {}
        timeout = aiohttp.ClientTimeout(
            connect=self._config.conn_timeout_s, total=self._config.read_timeout_s
        )
        return aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers=headers,
            trace_configs=self._trace_configs,
        )

    async def aclose(self) -> None:
        assert self._client
        await self._client.close()

    async def _reload_http_client(self) -> None:
        await self.aclose()
        self._token = None
        self._client = await self._create_http_client()

    async def init_if_needed(self) -> None:
        if not self._client or self._client.closed:
            self._client = await self._create_http_client()

    def _get_pods_url(self, namespace: str) -> URL:
        if namespace:
            return self._config.url / "api/v1/namespaces" / namespace / "pods"
        return self._config.url / "api/v1/pods"

    @trace
    async def get_node(self, name: str) -> Node:
        await self.init_if_needed()
        assert self._client
        url = self._config.url / "api/v1/nodes" / name
        payload = await self._request(method="get", url=url)
        assert payload["kind"] == "Node"
        return Node.from_payload(payload)

    @trace
    async def get_pods(
        self, namespace: str = "", field_selector: str = "", label_selector: str = ""
    ) -> Sequence[Pod]:
        await self.init_if_needed()
        assert self._client
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

    async def _request(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        await self.init_if_needed()
        assert self._client, "client is not initialized"
        doing_retry = kwargs.pop("doing_retry", False)
        async with self._client.request(*args, **kwargs) as resp:
            payload = await resp.json()
        try:
            self._raise_for_status(payload)
        except KubeClientUnauthorized:
            if doing_retry:
                raise
            # K8s SA's token might be stale, need to refresh it and retry
            await self._reload_http_client()
            kwargs["doing_retry"] = True
            payload = await self._request(*args, **kwargs)
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

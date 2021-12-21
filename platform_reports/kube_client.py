from __future__ import annotations

from collections.abc import Sequence
import enum
import functools
import logging
import ssl
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Any, Awaitable, Callable, Optional, TypeVar, cast

import aiohttp
from neuro_logging import trace
from yarl import URL

from .config import KubeClientAuthType, KubeConfig


logger = logging.getLogger(__name__)

T = TypeVar("T", bound=Callable[..., Awaitable[Any]])


def reconnect(func: T) -> T:
    @functools.wraps(func)
    async def new_func(*args: Any, **kwargs: Any) -> Any:
        try:
            return await func(*args, **kwargs)
        except aiohttp.ClientConnectionError as ex:
            logger.warning("Connection error to Kubernetes API", exc_info=ex)
            return await func(*args, **kwargs)

    return cast(T, new_func)


@dataclass(frozen=True)
class Metadata:
    name: str
    labels: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "Metadata":
        return cls(name=payload["name"], labels=payload.get("labels", {}))


@dataclass(frozen=True)
class Node:
    metadata: Metadata

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "Node":
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
    def from_payload(cls, payload: dict[str, Any]) -> "PodStatus":
        return cls(phase=PodPhase(payload.get("phase", "Unknown")))


@dataclass(frozen=True)
class Resources:
    cpu_m: int = 0
    memory_mb: int = 0
    gpu: int = 0

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "Resources":
        return cls(
            cpu_m=cls._parse_cpu_m(payload.get("cpu", "0")),
            memory_mb=cls._parse_memory_mb(payload.get("memory", "0Mi")),
            gpu=int(payload.get("nvidia.com/gpu", 0)),
        )

    @classmethod
    def _parse_cpu_m(cls, value: str) -> int:
        if value.endswith("m"):
            return int(value[:-1])
        return int(float(value) * 1000)

    @classmethod
    def _parse_memory_mb(cls, value: str) -> int:
        if value.endswith("Gi"):
            return int(value[:-2]) * 1024
        if value.endswith("Mi"):
            return int(value[:-2])
        raise ValueError("Memory unit is not supported")


@dataclass(frozen=True)
class Container:
    name: str
    resource_requests: Resources = field(default_factory=Resources)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "Container":
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
    def from_payload(cls, payload: dict[str, Any]) -> "Pod":
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

    async def __aenter__(self) -> "KubeClient":
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
            token = self._config.token
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

    def _get_pods_url(self, namespace: str) -> URL:
        if namespace:
            return self._config.url / "api/v1/namespaces" / namespace / "pods"
        return self._config.url / "api/v1/pods"

    @trace
    @reconnect
    async def get_node(self, name: str) -> Node:
        assert self._client
        async with self._client.get(
            self._config.url / "api/v1/nodes" / name
        ) as response:
            response.raise_for_status()
            payload = await response.json()
            assert payload["kind"] == "Node"
            return Node.from_payload(payload)

    @trace
    @reconnect
    async def get_pods(
        self, namespace: str = "", field_selector: str = "", label_selector: str = ""
    ) -> Sequence[Pod]:
        assert self._client
        params: dict[str, str] = {}
        if field_selector:
            params["fieldSelector"] = field_selector
        if label_selector:
            params["labelSelector"] = label_selector
        async with self._client.get(
            self._get_pods_url(namespace), params=params or None
        ) as response:
            response.raise_for_status()
            payload = await response.json()
            assert payload["kind"] == "PodList"
            return [Pod.from_payload(i) for i in payload["items"]]

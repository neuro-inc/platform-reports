from __future__ import annotations

import abc
import asyncio
import enum
import json
import logging
import ssl
from collections.abc import AsyncIterator, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Any

import aiohttp
from neuro_logging import trace
from yarl import URL

from .config import KubeClientAuthType, KubeConfig

logger = logging.getLogger(__name__)


class KubeClientException(Exception):
    pass


class ResourceGoneException(KubeClientException):
    pass


class WatchEventType(str, enum.Enum):
    ADDED = "ADDED"
    MODIFIED = "MODIFIED"
    DELETED = "DELETED"
    ERROR = "ERROR"


@dataclass(frozen=True)
class WatchBookmarkEvent:
    resource_version: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> WatchBookmarkEvent:
        return cls(
            resource_version=payload["object"]["metadata"]["resourceVersion"],
        )

    @classmethod
    def is_bookmark(cls, payload: dict[str, Any]) -> bool:
        return "BOOKMARK" == payload["type"].upper()


@dataclass(frozen=True)
class WatchEvent:
    type: WatchEventType
    resource: dict[str, Any]

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> WatchEvent:
        return cls(type=WatchEventType(payload["type"]), resource=payload["object"])

    @classmethod
    def is_error(cls, payload: dict[str, Any]) -> bool:
        return WatchEventType.ERROR == payload["type"].upper()

    @classmethod
    def added(cls, resource: dict[str, Any]) -> WatchEvent:
        return cls(type=WatchEventType.ADDED, resource=resource)

    @classmethod
    def modified(cls, resource: dict[str, Any]) -> WatchEvent:
        return cls(type=WatchEventType.MODIFIED, resource=resource)

    @classmethod
    def deleted(cls, resource: dict[str, Any]) -> WatchEvent:
        return cls(type=WatchEventType.DELETED, resource=resource)


@dataclass(frozen=True)
class Metadata:
    name: str
    labels: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> Metadata:
        return cls(name=payload["name"], labels=payload.get("labels", {}))


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
    image: str
    resource_requests: Resources = field(default_factory=Resources)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> Container:
        return cls(
            name=payload["name"],
            image=payload["image"],
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


@dataclass(frozen=True)
class ListResult:
    resource_version: str
    items: list[dict[str, Any]]

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> ListResult:
        return ListResult(
            resource_version=payload["metadata"]["resourceVersion"],
            items=payload["items"],
        )


@dataclass(frozen=True)
class PodListResult:
    resource_version: str
    items: list[Pod]


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

    def _get_pods_url(self, namespace: str | None) -> URL:
        if namespace:
            return self._config.url / "api/v1/namespaces" / namespace / "pods"
        return self._config.url / "api/v1/pods"

    def _raise_for_status(self, payload: dict[str, Any]) -> None:
        if payload.get("kind") != "Status":
            return
        if payload["status"] == "Failure":
            reason = payload.get("reason")
            if reason == "Gone":
                raise ResourceGoneException(payload["reason"])
            raise KubeClientException(payload["reason"])

    async def _watch(
        self,
        url: URL,
        params: dict[str, str] | None = None,
        resource_version: str | None = None,
    ) -> AsyncIterator[WatchEvent | WatchBookmarkEvent]:
        params = params or {}
        params.update(watch="true", allowWatchBookmarks="true")
        if resource_version:
            params["resourceVersion"] = resource_version
        assert self._client
        async with self._client.get(
            url, params=params, timeout=aiohttp.ClientTimeout()
        ) as response:
            if response.status == 410:
                raise ResourceGoneException
            async for line in response.content:
                payload = json.loads(line)
                if WatchEvent.is_error(payload):
                    self._raise_for_status(payload["object"])
                if WatchBookmarkEvent.is_bookmark(payload):
                    yield WatchBookmarkEvent.from_payload(payload)
                else:
                    yield WatchEvent.from_payload(payload)

    @trace
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
    async def get_raw_pods(
        self,
        namespace: str | None = None,
        field_selector: str | None = None,
        label_selector: str | None = None,
    ) -> ListResult:
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
            self._raise_for_status(payload)
            assert payload["kind"] == "PodList"
            return ListResult.from_payload(payload)

    @trace
    async def get_pods(
        self,
        namespace: str | None = None,
        field_selector: str | None = None,
        label_selector: str | None = None,
    ) -> PodListResult:
        result = await self.get_raw_pods(
            namespace=namespace,
            field_selector=field_selector,
            label_selector=label_selector,
        )
        return PodListResult(
            resource_version=result.resource_version,
            items=[Pod.from_payload(pod) for pod in result.items],
        )

    async def watch_pods(
        self,
        namespace: str | None = None,
        field_selector: str | None = None,
        label_selector: str | None = None,
        resource_version: str | None = None,
    ) -> AsyncIterator[WatchEvent | WatchBookmarkEvent]:
        params: dict[str, str] = {}
        if field_selector:
            params["fieldSelector"] = field_selector
        if label_selector:
            params["labelSelector"] = label_selector
        async for event in self._watch(
            self._get_pods_url(namespace), resource_version=resource_version
        ):
            yield event


class EventListener:
    @abc.abstractmethod
    async def init(self, resources: list[dict[str, Any]]) -> None:
        pass

    @abc.abstractmethod
    async def handle(self, event: WatchEvent) -> None:
        pass


class Watcher(abc.ABC):
    def __init__(self, kube_client: KubeClient) -> None:
        self._kube_client = kube_client
        self._listeners: list[EventListener] = []
        self._watcher_task: asyncio.Task[None] | None = None

    def subscribe(self, listener: EventListener) -> None:
        if self._watcher_task is not None:
            raise Exception("Subscription is not possible after watcher start")
        self._listeners.append(listener)

    async def __aenter__(self) -> "Watcher":
        await self.start()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.stop()

    async def start(self) -> None:
        result = await self.list()
        for listener in self._listeners:
            await listener.init(result.items)
        self._watcher_task = asyncio.create_task(self._run(result.resource_version))

    async def stop(self) -> None:
        if self._watcher_task is None:
            return
        self._watcher_task.cancel()
        with suppress(asyncio.CancelledError):
            await self._watcher_task
        self._watcher_task = None
        self._listeners.clear()

    async def _run(self, resource_version: str) -> None:
        while True:
            try:
                async for event in self.watch(resource_version):
                    if isinstance(event, WatchBookmarkEvent):
                        resource_version = event.resource_version
                        continue
                    for listener in self._listeners:
                        await listener.handle(event)
            except ResourceGoneException as exc:
                logger.warning("Resource gone", exc_info=exc)
            except aiohttp.ClientError as exc:
                logger.warning("Watch client error", exc_info=exc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Unhandled error", exc_info=exc)

    @abc.abstractmethod
    async def list(self) -> ListResult:
        pass

    @abc.abstractmethod
    async def watch(
        self, resource_version: str
    ) -> AsyncIterator[WatchEvent | WatchBookmarkEvent]:
        yield  # type: ignore


class PodWatcher(Watcher):
    def __init__(
        self,
        kube_client: KubeClient,
        namespace: str | None = None,
        field_selector: str | None = None,
        label_selector: str | None = None,
    ) -> None:
        super().__init__(kube_client)
        self._kwargs = {
            "namespace": namespace,
            "field_selector": field_selector,
            "label_selector": label_selector,
        }

    async def list(self) -> ListResult:
        return await self._kube_client.get_raw_pods(**self._kwargs)

    async def watch(
        self, resource_version: str
    ) -> AsyncIterator[WatchEvent | WatchBookmarkEvent]:
        async for event in self._kube_client.watch_pods(
            resource_version=resource_version, **self._kwargs
        ):
            yield event

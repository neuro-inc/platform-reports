from __future__ import annotations

import asyncio
import enum
import logging
import ssl
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

import aiohttp
from dateutil.parser import parse as parse_date
from yarl import URL

from .config import KubeClientAuthType, KubeConfig


LOGGER = logging.getLogger(__name__)


class KubeClientError(Exception):
    pass


class KubeClientUnauthorized(KubeClientError):
    pass


@dataclass(frozen=True)
class Metadata:
    name: str
    creation_timestamp: datetime
    labels: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> Metadata:
        return cls(
            name=payload["name"],
            creation_timestamp=parse_date(payload["creationTimestamp"]),
            labels=payload.get("labels", {}),
        )


@dataclass(frozen=True)
class Node:
    metadata: Metadata

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> Node:
        return cls(metadata=Metadata.from_payload(payload["metadata"]))


class PodPhase(enum.StrEnum):
    PENDING = "Pending"
    RUNNING = "Running"
    SUCCEEDED = "Succeeded"
    FAILED = "Failed"
    UNKNOWN = "Unknown"

    @classmethod
    def parse(cls, value: str) -> Self:
        try:
            return cls(value)
        except (KeyError, ValueError):
            return cls(cls.UNKNOWN.value)


@dataclass(frozen=True)
class ContainerStatus:
    state: Mapping[str, Any]

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> ContainerStatus:
        return cls(state=payload.get("state") or {})

    @property
    def started_at(self) -> datetime | None:
        for state in self.state.values():
            if started_at := state.get("startedAt"):
                return parse_date(started_at)
        return None

    @property
    def finished_at(self) -> datetime | None:
        for state in self.state.values():
            if finished_at := state.get("finishedAt"):
                return parse_date(finished_at)
        return None

    @property
    def is_waiting(self) -> bool:
        return not self.state or "waiting" in self.state

    @property
    def is_running(self) -> bool:
        return bool(self.state) and "running" in self.state

    @property
    def is_terminated(self) -> bool:
        return bool(self.state) and "terminated" in self.state


@dataclass(frozen=True)
class PodCondition:
    # https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/#pod-conditions

    class Type(enum.StrEnum):
        UNKNOWN = "Unknown"
        POD_SCHEDULED = "PodScheduled"
        POD_READY_TO_START_CONTAINERS = "PodReadyToStartContainers"
        CONTAINERS_READY = "ContainersReady"
        INITIALIZED = "Initialized"
        READY = "Ready"
        DISRUPTION_TARGET = "DisruptionTarget"
        POD_RESIZE_PENDING = "PodResizePending"
        POD_RESIZE_IN_PROGRESS = "PodResizeInProgress"

        @classmethod
        def parse(cls, value: str) -> Self:
            try:
                return cls(value)
            except (KeyError, ValueError):
                return cls(cls.UNKNOWN.value)

    type: Type
    last_transition_time: datetime
    status: bool | None = None
    message: str = ""
    reason: str = ""

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> Self:
        return cls(
            type=cls.Type.parse(payload["type"]),
            last_transition_time=parse_date(payload["lastTransitionTime"]),
            status=cls._parse_status(payload["status"]),
            message=payload.get("message", ""),
            reason=payload.get("reason", ""),
        )

    @staticmethod
    def _parse_status(value: str) -> bool | None:
        if value == "Unknown":
            return None
        if value == "True":
            return True
        if value == "False":
            return False
        msg = f"Invalid status {value!r}"
        raise ValueError(msg)


@dataclass(frozen=True)
class PodStatus:
    phase: PodPhase
    container_statuses: Sequence[ContainerStatus] = field(default_factory=list)
    conditions: Sequence[PodCondition] = field(default_factory=list)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> PodStatus:
        return cls(
            phase=PodPhase(payload.get("phase", PodPhase.UNKNOWN)),
            container_statuses=[
                ContainerStatus.from_payload(p)
                for p in payload.get("containerStatuses", ())
            ],
            conditions=[
                PodCondition.from_payload(p) for p in payload.get("conditions", ())
            ],
        )

    @property
    def is_pending(self) -> bool:
        return self.phase == PodPhase.PENDING

    @property
    def is_scheduled(self) -> bool:
        if self.phase not in (PodPhase.PENDING, PodPhase.UNKNOWN):
            return True
        for condition in self.conditions:
            if condition.type == PodCondition.Type.POD_SCHEDULED:
                return bool(condition.status)
        return False

    @property
    def is_terminated(self) -> bool:
        return self.phase in (PodPhase.SUCCEEDED, PodPhase.FAILED)

    @property
    def finish_date(self) -> datetime:
        if not self.is_terminated:
            msg = "Pod has not finished yet"
            raise ValueError(msg)
        finish_date = None
        for container_status in self.container_statuses:
            if finished_at := container_status.finished_at:
                finish_date = max(finish_date or finished_at, finished_at)
            else:
                msg = "Pod has not finished yet"
                raise ValueError(msg)
        if finish_date is None:
            msg = "Pod has not finished yet"
            raise ValueError(msg)
        return finish_date.astimezone(UTC)

    def get_condition(self, type_: PodCondition.Type) -> PodCondition:
        for condition in self.conditions:
            if condition.type == type_:
                return condition
        msg = f"Condition {type_!r} not found"
        raise ValueError(msg)


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

    def _create_ssl_context(self) -> ssl.SSLContext | bool:
        if self._config.url.scheme != "https":
            return False
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

    async def __aenter__(self) -> Self:
        await self._init()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def _init(self) -> None:
        connector = aiohttp.TCPConnector(
            limit=self._config.conn_pool_size, ssl=self._create_ssl_context()
        )
        if self._config.token_path:
            self._token = await asyncio.to_thread(
                Path(self._config.token_path).read_text
            )
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
                token = await asyncio.to_thread(Path(self._config.token_path).read_text)
                if token != self._token:
                    self._token = token
                    LOGGER.info("Kube token was refreshed")
            except Exception as exc:
                LOGGER.exception("Failed to update kube token: %s", exc)
            await asyncio.sleep(self._config.token_update_interval_s)

    async def aclose(self) -> None:
        if self._client:
            await self._client.close()
        if self._token_updater_task:
            self._token_updater_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._token_updater_task
            self._token_updater_task = None

    def _get_pods_url(self, namespace: str | None = None) -> URL:
        if namespace:
            return self._config.url / "api/v1/namespaces" / namespace / "pods"
        return self._config.url / "api/v1/pods"

    async def get_node(self, name: str) -> Node:
        url = self._config.url / "api/v1/nodes" / name
        payload = await self._request(method="get", url=url)
        assert payload["kind"] == "Node"
        return Node.from_payload(payload)

    async def get_nodes(self, label_selector: str | None = None) -> list[Node]:
        url = self._config.url / "api/v1/nodes"
        params: dict[str, str] = {}
        if label_selector:
            params["labelSelector"] = label_selector
        payload = await self._request(method="get", url=url, params=params)
        assert payload["kind"] == "NodeList"
        return [Node.from_payload(i) for i in payload["items"]]

    async def create_raw_pod(
        self, namespace: str, raw_pod: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._request(
            method="post", url=self._get_pods_url(namespace), json=raw_pod
        )

    async def get_pods(
        self,
        namespace: str | None = None,
        field_selector: str | None = None,
        label_selector: str | None = None,
    ) -> list[Pod]:
        params: dict[str, str] = {}
        if field_selector:
            params["fieldSelector"] = field_selector
        if label_selector:
            params["labelSelector"] = label_selector
        payload = await self._request(
            method="get", url=self._get_pods_url(namespace), params=params
        )
        assert payload["kind"] == "PodList"
        return [Pod.from_payload(i) for i in payload["items"]]

    async def get_pod(self, namespace: str, pod_name: str) -> Pod:
        payload = await self._request(
            method="GET", url=self._get_pods_url(namespace) / pod_name
        )
        assert payload["kind"] == "Pod"
        return Pod.from_payload(payload)

    async def delete_pod(self, namespace: str, pod_name: str) -> None:
        await self._request(
            method="DELETE", url=self._get_pods_url(namespace) / pod_name
        )

    async def wait_pod_is_running(
        self,
        namespace: str,
        name: str,
        *,
        timeout: float = 60,  # noqa: ASYNC109
        interval: float = 1,
    ) -> None:
        await asyncio.wait_for(
            self._wait_pod_is_running(namespace, name, interval=interval), timeout
        )

    async def _wait_pod_is_running(
        self, namespace: str, name: str, *, interval: float = 1
    ) -> None:
        while True:
            pod = await self.get_pod(namespace, name)
            if not pod.status.is_pending:
                return
            await asyncio.sleep(interval)

    async def wait_pod_is_terminated(
        self,
        namespace: str,
        name: str,
        *,
        timeout: float = 60,  # noqa: ASYNC109
        interval: float = 1,
    ) -> None:
        await asyncio.wait_for(
            self._wait_pod_is_terminated(namespace, name, interval=interval), timeout
        )

    async def _wait_pod_is_terminated(
        self, namespace: str, name: str, *, interval: float = 1
    ) -> None:
        while True:
            pod = await self.get_pod(namespace, name)
            if pod.status.is_terminated:
                return
            await asyncio.sleep(interval)

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
